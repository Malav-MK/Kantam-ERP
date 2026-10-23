"""
Approval requests for the workflow steps that need a second person (e.g. a sales
order amendment after lock, a stock-count adjustment, an extra issue beyond the
plan, a quotation margin exception). The workflow creates the request with
services.request_approval(); checkers decide here and the registered approval
handler applies it.

The generic maker / checker policies (holding any create / edit / action until a
checker approved it) have been removed: changes apply directly and stay audited.
"""
from django.db import transaction
from django.utils import timezone

from . import audit
from .api import BusinessRuleError
from .models import ApprovalRequest
from .rbac import has_perm, is_owner
from .services import notify, users_with_perm


def pending_for(resource_key, object_id):
    return list(ApprovalRequest.objects.filter(resource=resource_key, object_id=str(object_id), status="pending")
                .values_list("id", flat=True)[:5])


def decide(approval_id, user, approve, note=""):
    from .services import APPROVAL_HANDLERS

    with transaction.atomic():
        approval = ApprovalRequest.objects.select_for_update().get(pk=approval_id)
        if approval.status != "pending":
            raise BusinessRuleError(f"This request is already {approval.get_status_display().lower()}.", "invalid_state")
        if approval.expires_at and approval.expires_at < timezone.now():
            approval.status = "expired"
            approval.save(update_fields=["status", "updated_at"])
            return approval
        if approval.created_by_id == user.pk and not is_owner(user):
            raise BusinessRuleError("Maker/checker: you cannot approve or reject your own request.", "self_approval")
        if any(a.get("user") == str(user.pk) for a in approval.approvals):
            raise BusinessRuleError("You have already approved this request; another checker is required.", "duplicate_checker")
        if not has_perm(user, approval.required_permission):
            raise BusinessRuleError("You are not an authorised checker for this request.", "not_authorised")
        now = timezone.now()
        if not approve:
            if len(note.strip()) < 5:
                raise BusinessRuleError("Give a reason for rejection.", "reason_required")
            approval.status, approval.decided_by, approval.decided_at, approval.decision_note = "rejected", user, now, note
            approval.row_version += 1
            approval.save()
            audit.record("approval_rejected", approval.resource, object_id=approval.object_id or "-", doc_no=approval.doc_no,
                         changes={"approval": str(approval.pk)}, reason=note, approval_ref=approval.pk)
            _tell_maker(approval, f"Rejected: {approval.summary}", note)
            return approval
        approval.approvals = approval.approvals + [{"user": str(user.pk), "name": user.full_name, "at": now.isoformat(), "note": note}]
        if len(approval.approvals) < approval.required_approvals:
            approval.row_version += 1
            approval.save()
            audit.record("approval_step", approval.resource, object_id=approval.object_id or "-", doc_no=approval.doc_no,
                         changes={"approval": str(approval.pk), "approved": len(approval.approvals), "required": approval.required_approvals},
                         reason=note, approval_ref=approval.pk)
            others = [u for u in users_with_perm(approval.required_permission)
                      if u.pk != approval.created_by_id and str(u.pk) not in {a["user"] for a in approval.approvals}]
            notify(others, f"Further approval needed ({len(approval.approvals)}/{approval.required_approvals}): {approval.summary}",
                   severity="warning", resource="core.approvalrequest", object_id=approval.pk,
                   dedupe_key=f"approval:{approval.pk}:{len(approval.approvals)}")
            return approval
        # Final approval -> execute
        if approval.kind in APPROVAL_HANDLERS:
            APPROVAL_HANDLERS[approval.kind](approval, user)
        approval.status, approval.decided_by, approval.decided_at, approval.decision_note = "approved", user, now, note
        approval.executed_at = now
        approval.row_version += 1
        approval.save()
        audit.record("approval_approved", approval.resource, object_id=approval.object_id or "-", doc_no=approval.doc_no,
                     changes={"approval": str(approval.pk), "checkers": [a["name"] for a in approval.approvals]}, reason=note,
                     approval_ref=approval.pk)
    _tell_maker(approval, f"Approved and applied: {approval.summary}", note)
    return approval


def withdraw(approval_id, user, note=""):
    with transaction.atomic():
        approval = ApprovalRequest.objects.select_for_update().get(pk=approval_id)
        if approval.created_by_id != user.pk:
            raise BusinessRuleError("Only the maker can withdraw a request.", "not_authorised")
        if approval.status != "pending":
            raise BusinessRuleError("Only pending requests can be withdrawn.", "invalid_state")
        approval.status, approval.decision_note = "withdrawn", note
        approval.save()
        audit.record("approval_withdrawn", approval.resource, object_id=approval.object_id or "-", changes={"approval": str(approval.pk)},
                     reason=note, approval_ref=approval.pk)
    return approval


def expire_pending(now=None):
    now = now or timezone.now()
    n = 0
    for a in ApprovalRequest.objects.filter(status="pending", expires_at__lt=now):
        a.status = "expired"
        a.save(update_fields=["status", "updated_at"])
        _tell_maker(a, f"Approval request expired: {a.summary}", "No checker decided in time; resubmit if still needed.")
        n += 1
    return n


def _tell_maker(approval, title, body):
    if approval.created_by_id:
        notify([approval.created_by], title, body=body or "", resource="core.approvalrequest", object_id=approval.pk,
               dedupe_key=f"approval-outcome:{approval.pk}:{approval.status}")
