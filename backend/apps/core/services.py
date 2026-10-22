"""Cross-department services: notifications, approvals (maker/checker) and outbox."""
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from . import audit
from .api import BusinessRuleError
from .models import ApprovalRequest, Notification, OutboxMessage, Permission, User, UserRole

APPROVAL_HANDLERS = {}


def approval_handler(kind):
    def deco(fn):
        APPROVAL_HANDLERS[kind] = fn
        return fn
    return deco


def users_with_perm(code):
    now = timezone.now()
    role_ids = Permission.objects.filter(code=code).values_list("roles__id", flat=True)
    user_ids = (
        UserRole.objects.filter(role_id__in=list(role_ids), valid_from__lte=now)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))
        .values_list("user_id", flat=True)
    )
    return User.objects.filter(pk__in=list(user_ids), is_active=True)


def notify(users, title, *, body="", severity="info", resource="", object_id="", next_action="", due_at=None, dedupe_key=""):
    """Create in-app alerts. A dedupe key prevents repeated alerts without a change in condition (p.18)."""
    created = 0
    for user in users:
        if user is None:
            continue
        try:
            with transaction.atomic():
                Notification.objects.create(
                    user=user, title=title[:200], body=body, severity=severity, resource=resource,
                    object_id=str(object_id or ""), next_action=next_action[:200], due_at=due_at, dedupe_key=dedupe_key[:160],
                )
                created += 1
        except IntegrityError:
            continue
    return created


def notify_perm(code, title, **kwargs):
    return notify(users_with_perm(code), title, **kwargs)


def request_approval(*, kind, resource, obj, summary, payload, reason, required_permission, requester, required_approvals=1, expires_hours=72):
    """Workflow-specific approval (e.g. post-lock SO amendment). Decided by maker_checker.decide()."""
    from datetime import timedelta

    approval = ApprovalRequest.objects.create(
        kind=kind, resource=resource, object_id=str(obj.pk), object_version=getattr(obj, "row_version", None),
        doc_no=getattr(obj, "doc_no", "") or "", summary=summary[:300], payload=audit._jsonable(payload),
        changes=audit._jsonable({k: [payload.get("old", {}).get(k), v] for k, v in (payload.get("new") or {}).items()}),
        reason=reason, required_permission=required_permission, required_approvals=required_approvals,
        expires_at=timezone.now() + timedelta(hours=expires_hours), created_by=requester,
    )
    audit.record("approval_requested", resource, obj, changes={"approval": str(approval.pk), "kind": kind, "payload": payload}, reason=reason,
                 approval_ref=approval.pk)
    approvers = [u for u in users_with_perm(required_permission) if u.pk != requester.pk]
    notify(
        approvers, f"Approval needed: {summary}", severity="warning", resource="core.approvalrequest",
        object_id=approval.pk, next_action="Review and approve or reject", dedupe_key=f"approval:{approval.pk}",
    )
    return approval


def decide_approval(approval, user, approve, note=""):
    from .maker_checker import decide
    return decide(approval.pk, user, approve, note)


def draft_outbox(*, channel, recipient, template, payload, source_resource, source_id, source_version=1, dedupe_key):
    """Outbound WhatsApp/email starts as a draft; nothing leaves the plant without approval (p.50)."""
    msg, created = OutboxMessage.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults=dict(
            channel=channel, recipient=recipient, template=template, payload=audit._jsonable(payload),
            source_resource=source_resource, source_id=str(source_id), source_version=source_version,
        ),
    )
    # A newer source version supersedes unsent drafts for the same source (no stale quantities/dates).
    if created:
        OutboxMessage.objects.filter(
            source_resource=source_resource, source_id=str(source_id), template=template,
            status__in=OutboxMessage.UNSENT, source_version__lt=source_version,
        ).exclude(pk=msg.pk).update(status="suppressed", last_error="Superseded by a newer source version")
    return msg


def suppress_stale_outbox(source_resource, source_id, reason):
    """The source was amended after a message was drafted: unsent drafts would carry old
    quantities/dates, so they are suppressed (kept for history) rather than sent."""
    stale = list(OutboxMessage.objects.filter(source_resource=source_resource, source_id=str(source_id), status__in=OutboxMessage.UNSENT))
    for msg in stale:
        msg.status, msg.last_error = "suppressed", reason
        msg.save(update_fields=["status", "last_error", "updated_at"])
        audit.record("outbox_suppressed", "core.outboxmessage", msg, reason=reason)
    return len(stale)


# Provider callbacks may arrive twice or out of order; they can only move a message forward.
_DELIVERY_RANK = {"approved": 0, "queued": 1, "accepted": 2, "failed": 2, "delivered": 3, "read": 4}


def record_delivery_status(msg, status, *, provider_id="", error=""):
    """Apply a (verified) provider status callback. Returns True if the state changed.
    Duplicate or late callbacks never regress a final state or trigger a resend."""
    if status not in _DELIVERY_RANK:
        raise ValueError(f"unknown delivery status {status}")
    if msg.status in OutboxMessage.FINAL or msg.status == "draft":
        return False
    if _DELIVERY_RANK[status] < _DELIVERY_RANK.get(msg.status, 0):
        return False
    if status == msg.status or (status == "failed" and msg.status in {"delivered", "read"}):
        return False
    msg.status = status
    if provider_id:
        msg.provider_id = provider_id
    if status == "failed":
        msg.last_error = error or "Provider reported failure"
    msg.save()
    audit.record(f"outbox_{status}", "core.outboxmessage", msg)
    return True
