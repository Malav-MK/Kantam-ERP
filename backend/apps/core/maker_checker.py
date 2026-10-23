"""
Maker / checker (four-eyes) control.

Flow
1. A maker submits a create / edit / workflow action through the normal API.
2. If an active ApprovalPolicy matches, the operation is **dry-run** inside a
   savepoint (so every business rule and permission is validated now, and the
   exact proposed changes are captured), then rolled back. An ApprovalRequest
   is stored and the API answers 202 "pending approval". Nothing takes effect.
3. Checkers (holding the policy's checker permission, never the maker, each
   person counted once) approve. When the required number is reached the
   operation is re-executed **as the maker**: the maker must still hold the
   permission and still see the record, and the record must be unchanged since
   submission (otherwise the request becomes "stale" and must be resubmitted).
4. Audit entries of the executed change carry the approval reference, and the
   approval itself records maker, every checker, timestamps and notes.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from . import audit
from .api import BusinessRuleError
from .models import ApprovalPolicy, ApprovalRequest, User
from .rbac import has_perm, is_owner
from .services import notify, users_with_perm


class _DryRun(Exception):
    def __init__(self, changes, result_label=""):
        self.changes = changes
        self.result_label = result_label


class ExecRequest:
    """Minimal request used to execute an approved operation as the maker."""

    def __init__(self, user, data, approval):
        self.user = user
        self.data = data
        self.query_params = {}
        self.method = "POST"
        self.FILES = {}
        self.headers = {}
        self.zt_client_ip = None
        self.zt_session = None
        self.zt_device = None
        self.maker_checker_exec = approval


def rules_for(resource_key):
    """Public description of active rules (UI hint only - enforcement is in guard())."""
    return [
        {"operation": p.operation, "action": p.action, "condition": p.condition, "fields": p.fields,
         "amount_threshold": str(p.amount_threshold) if p.amount_threshold is not None else None,
         "approvals": p.required_approvals, "name": p.name}
        for p in ApprovalPolicy.objects.filter(resource=resource_key, active=True)
    ]


def pending_for(resource_key, object_id):
    return list(ApprovalRequest.objects.filter(resource=resource_key, object_id=str(object_id), status="pending")
                .values_list("id", flat=True)[:5])


def _candidates(res, operation, act):
    qs = ApprovalPolicy.objects.filter(resource=res.key, operation=operation, active=True)
    if operation == "action":
        qs = qs.filter(action=act.name)
    return list(qs.order_by("-required_approvals"))


def _amount(res, request, obj, raw):
    fn = getattr(res, "approval_amount", None)
    if not fn:
        return None
    try:
        value = fn(request, obj, raw)
        return Decimal(str(value)) if value is not None else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _matches(policy, changes, amount):
    if policy.condition == "always":
        return True
    if policy.condition == "fields_changed":
        return bool(set(policy.fields or []) & set(changes or {}))
    if policy.condition == "amount_over":
        return amount is not None and policy.amount_threshold is not None and amount > policy.amount_threshold
    return False


def _run(request, res, operation, obj, raw, act):
    from .resource_api import perform_action, perform_create, perform_update

    if operation == "create":
        new, snap = perform_create(request, res, raw)
        return new, {k: [None, v] for k, v in snap.items() if v not in (None, "", [], {})}
    if operation == "update":
        return perform_update(request, res, obj, raw)
    return perform_action(request, res, act, obj, raw)


def guard(request, res, operation, *, obj=None, raw=None, act=None):
    """Return a 202 Response when the operation must wait for a checker, else None."""
    if getattr(request, "maker_checker_exec", None) is not None:
        return None  # executing an already-approved request
    if is_owner(request.user):
        return None  # the owner is the super administrator: changes apply directly (and are still audited)
    candidates = _candidates(res, operation, act)
    if not candidates:
        return None
    raw = dict(raw or {})
    amount = _amount(res, request, obj, raw)
    # Dry run: validates rules/permissions now and captures the exact proposed change.
    try:
        with transaction.atomic():
            result, changes = _run(request, res, operation, obj, raw, act)
            raise _DryRun(changes, str(result) if result is not None and not isinstance(result, dict) else "")
    except _DryRun as dry:
        changes = dry.changes
    policy = next((p for p in candidates if _matches(p, changes, amount)), None)
    if obj is not None:
        obj.refresh_from_db()
    if policy is None:
        return None

    reason = str(raw.get("approval_reason") or raw.get("reason") or "").strip()
    if policy.reason_required and len(reason) < 5:
        raise BusinessRuleError(f"'{policy.name}' needs checker approval - give a reason for this change (5+ characters).",
                                "approval_reason_required", {"policy": policy.name})
    object_id = str(obj.pk) if obj is not None else ""
    dup = ApprovalRequest.objects.filter(resource=res.key, object_id=object_id, operation=operation, action=act.name if act else "",
                                         status="pending", created_by=request.user)
    if obj is not None and dup.exists():
        raise BusinessRuleError("You already have a pending approval request for this change. Withdraw it first.", "approval_pending")

    label = act.label if act else {"create": f"Create {res.singular}", "update": f"Edit {res.singular}"}[operation]
    target = getattr(obj, "doc_no", "") or (str(obj) if obj is not None else "")
    approval = ApprovalRequest.objects.create(
        kind="policy", policy=policy, operation=operation, action=act.name if act else "", resource=res.key, object_id=object_id,
        object_version=getattr(obj, "row_version", None), doc_no=getattr(obj, "doc_no", "") or "",
        summary=f"{label}: {target}".strip(": ")[:300], payload={"data": audit._jsonable(raw)}, changes=audit._jsonable(changes or {}),
        amount=amount, reason=reason, required_permission=policy.checker_permission, required_approvals=max(1, min(policy.required_approvals, 3)),
        expires_at=timezone.now() + timedelta(hours=policy.expires_hours), created_by=request.user,
    )
    audit.record("approval_requested", res.key, obj, object_id=object_id or "-", changes={"approval": str(approval.pk), "policy": policy.name,
                 "proposed": approval.changes}, reason=reason, approval_ref=approval.pk)
    checkers = [u for u in users_with_perm(policy.checker_permission) if u.pk != request.user.pk]
    notify(checkers, f"Approval needed: {approval.summary}", body=f"Maker: {request.user.full_name}. Reason: {reason}", severity="warning",
           resource="core.approvalrequest", object_id=approval.pk, next_action="Review proposed change; approve or reject",
           dedupe_key=f"approval:{approval.pk}")
    return Response({
        "pending_approval": True, "approval": str(approval.pk), "policy": policy.name, "required_approvals": approval.required_approvals,
        "message": f"Submitted for checker approval ({policy.name}). Nothing changes until an authorised checker approves.",
    }, status=202)


def _execute(approval, checker):
    from .registry import REGISTRY
    from .resource_api import action_response  # noqa: F401 - keeps import graph explicit

    res = REGISTRY.get(approval.resource)
    maker = User.objects.get(pk=approval.created_by_id)
    if res is None or not maker.is_active:
        raise BusinessRuleError("The maker's account or the record type is no longer available.", "maker_inactive")
    op, act = approval.operation, (res.get_action(approval.action) if approval.action else None)
    needed = res.perm("create") if op == "create" else res.perm("update") if op == "update" else act.perm(res.key)
    if not has_perm(maker, needed):
        raise BusinessRuleError("The maker no longer holds the permission for this change. Reject the request.", "maker_not_authorised")
    req = ExecRequest(maker, dict(approval.payload.get("data") or {}), approval)
    obj = None
    if approval.object_id:
        obj = res.get_queryset(req).select_for_update().filter(pk=approval.object_id).first()
        if obj is None:
            raise BusinessRuleError("The record is no longer visible to the maker.", "record_unavailable")
        if approval.object_version is not None and getattr(obj, "row_version", None) != approval.object_version:
            return None  # stale: the record changed after submission
        if op == "update":
            req.data["row_version"] = obj.row_version
    previous = audit.request_context()
    audit.set_request_context(user=maker, approval_ref=str(approval.pk))
    try:
        result, changes = _run(req, res, op, obj, req.data, act)
    finally:
        audit.clear_request_context()
        audit.set_request_context(**previous)
    if hasattr(result, "pk"):
        return {"resource": res.key, "id": str(result.pk), "label": str(result), "changes": audit._jsonable(changes)}
    if isinstance(result, tuple):
        result = result[0]
    return {"resource": res.key, "result": audit._jsonable(result) if isinstance(result, dict) else str(result), "changes": audit._jsonable(changes)}


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
        if approval.kind == "policy":
            outcome = _execute(approval, user)
            if outcome is None:
                approval.status = "stale"
                approval.decided_by, approval.decided_at, approval.decision_note = user, now, "Record changed after submission"
                approval.save()
                _tell_maker(approval, f"Resubmit needed: {approval.summary}", "The record changed after you submitted; please review and resubmit.")
                return approval
            approval.result = outcome
        elif approval.kind in APPROVAL_HANDLERS:
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


DEFAULT_POLICIES = [
    # Security administration (four-eyes on identity changes)
    ("Grant role", "core.user", "action", "grant_role", "always", [], None, "security.roles.manage", 1),
    ("Revoke role", "core.user", "action", "revoke_role", "always", [], None, "security.roles.manage", 1),
    ("Reset user password", "core.user", "action", "set_password", "always", [], None, "security.users.manage", 1),
    ("Reset user MFA", "core.user", "action", "reset_mfa", "always", [], None, "security.users.manage", 1),
    ("Change role permissions", "core.role", "action", "set_permissions", "always", [], None, "security.roles.manage", 1),
    ("Reset role to default", "core.role", "action", "reset_default", "always", [], None, "security.roles.manage", 1),
    ("Create approval policy", "core.approvalpolicy", "create", "", "always", [], None, "approvals.policy.manage", 1),
    ("Change approval policy", "core.approvalpolicy", "update", "", "always", [], None, "approvals.policy.manage", 1),
    # Commercial master data
    ("Customer credit / terms change", "masters.customer", "update", "", "fields_changed",
     ["credit_limit", "payment_terms", "advance_required_pct", "freight_basis"], None, "sales.so.approve_amendment", 1),
    ("Supplier terms / GSTIN change", "masters.supplier", "update", "", "fields_changed", ["payment_terms", "gstin"], None, "purchase.po.approve", 1),
    ("Item standard cost change", "masters.item", "update", "", "fields_changed", ["standard_cost"], None, "commercial.cost.view", 1),
    ("Recipe approval", "masters.recipe", "action", "approve", "always", [], None, "masters.recipe.approve", 1),
    # Operations
    ("Opening stock entry", "inventory.stockbalance", "action", "opening_stock", "always", [], None, "inventory.adjust.approve", 1),
    ("Stock movement reversal", "inventory.stockmove", "action", "reverse", "always", [], None, "inventory.adjust.approve", 1),
    ("Closed-shift production correction", "production.productionreport", "action", "correct", "always", [], None, "production.correct_closed", 1),
    ("Cancel sales order", "sales.salesorder", "action", "cancel", "always", [], None, "sales.so.approve_amendment", 1),
    ("Cancel purchase order", "purchase.purchaseorder", "action", "cancel", "always", [], None, "purchase.po.approve", 1),
    ("High-value PO approval (owner co-sign)", "purchase.purchaseorder", "action", "approve", "amount_over", [], Decimal("500000"),
     "dashboard.owner.view", 1),
    ("Gate pass disposition", "gate.gatepass", "action", "dispose", "always", [], None, "gate.gatepass.approve", 1),
    # Follow-up discipline: lateness is never erased by silently moving a due date
    ("Task due-date revision", "tasks.task", "action", "revise_due", "always", [], None, "tasks.verify", 1),
]


def seed_default_policies():
    """Adds any default policy not yet present (matched by name). Existing ones - including
    ones an administrator deactivated or edited - are left untouched."""
    existing = set(ApprovalPolicy.objects.values_list("name", flat=True))
    created = 0
    for name, resource, op, action, cond, fields, threshold, perm, n in DEFAULT_POLICIES:
        if name in existing:
            continue
        ApprovalPolicy.objects.create(name=name, resource=resource, operation=op, action=action, condition=cond, fields=fields,
                                      amount_threshold=threshold, checker_permission=perm, required_approvals=n)
        created += 1
    return created
