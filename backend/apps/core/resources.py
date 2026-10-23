"""Administration resources: people lookup, users, roles, devices, sessions, approvals, outbox."""
from datetime import timedelta

from django.contrib.auth import password_validation
from django.db.models import Q
from django.utils import timezone

from . import audit
from .api import BusinessRuleError
from .auth_views import new_enrollment_code
from .models import ApprovalRequest, AuthSession, Department, Device, OutboxMessage, Role, Site, User, UserRole
from .rbac import effective_permissions, has_perm, is_owner
from .registry import Action, Resource, register

SEC_USERS = "security.users.manage"


def _protected_target(request, target):
    """Only an owner may act on an owner-level account (prevents IT-admin account takeover)."""
    from .rbac import role_levels
    return "owner" in role_levels(target) and not is_owner(request.user)


def _guard_target(request, target):
    if _protected_target(request, target):
        raise BusinessRuleError("Owner accounts can only be changed by an owner.", "protected_account")


def _user_delete_block(request, obj):
    if obj.pk == request.user.pk:
        return "You cannot delete your own account."
    if obj.role_grants.filter(role__level="owner").exists():
        return "Owner accounts cannot be deleted. Revoke the owner role first."
    return None


@register
class UserLookupResource(Resource):
    """Name-only projection of active users for assignment / host selection."""

    key = "core.userlookup"
    model = User
    label = "People"
    singular = "Person"
    module = "core"
    title_field = "full_name"
    list_fields = ["full_name", "department", "job_title"]
    search_fields = ["full_name", "username"]
    filter_fields = ["department"]
    ordering = ["full_name"]
    allow_create = False
    allow_update = False

    def scope(self, request, qs):
        return qs.filter(is_active=True)

    def delete_block_reason(self, request, obj):
        return _user_delete_block(request, obj)


@register
class UserAdminResource(Resource):
    key = "core.user"
    model = User
    canonical = False
    label = "Users"
    singular = "User"
    module = "core"
    title_field = "full_name"
    permission_map = {"view": SEC_USERS, "create": SEC_USERS, "update": SEC_USERS, "export": SEC_USERS}
    list_fields = ["username", "full_name", "department", "job_title", "is_active", "mfa_enabled", "locked_until", "last_login"]
    create_only_fields = ["username"]
    form_fields = ["full_name", "phone", "site", "department", "reporting_manager", "job_title", "is_active", "active_from", "active_until", "preferred_language"]
    readonly_fields = ["mfa_enabled", "failed_logins", "role_version", "created_at"]
    search_fields = ["username", "full_name"]
    filter_fields = ["department", "is_active"]
    ordering = ["full_name"]
    field_help = {"username": "Personal login name. Department passwords are never shared (brief p.27)."}
    help = {
        "why": "Every person has a named account so actions are attributable.",
        "boundary": "Seniority is not permission: grant roles explicitly. Passwords are never displayed or recoverable.",
    }

    def edit_block_reason(self, request, obj):
        if _protected_target(request, obj):
            return "Owner accounts can only be changed by an owner."

    def delete_block_reason(self, request, obj):
        return _user_delete_block(request, obj)
        return None

    def create(self, request, data):
        user = User.objects.create_user(data.pop("username"), password=None, **data)
        user.must_change_password = True
        user.save(update_fields=["must_change_password"])
        return user

    def after_update(self, request, obj, changes):
        if "is_active" in changes or "active_until" in changes:
            obj.bump_role_version()
            if not obj.is_active:
                AuthSession.objects.filter(user=obj, revoked_at__isnull=True).update(revoked_at=timezone.now(), revoke_reason="user_disabled")

    def extra_detail(self, request, obj):
        grants = UserRole.objects.filter(user=obj).select_related("role", "department")
        return {
            "grants": [
                {"id": str(g.id), "role": g.role.code, "role_name": g.role.name, "department": g.department.name if g.department else None,
                 "valid_from": g.valid_from, "valid_until": g.valid_until, "reason": g.reason}
                for g in grants
            ],
            "active_sessions": AuthSession.objects.filter(user=obj, revoked_at__isnull=True, expires_at__gt=timezone.now()).count(),
        }

    def _set_password(request, obj, data):
        _guard_target(request, obj)
        if obj.pk == request.user.pk:
            raise BusinessRuleError("Use change-password for your own account.", "invalid_state")
        pwd = str(data.get("password", ""))
        password_validation.validate_password(pwd, obj)
        obj.set_password(pwd)
        obj.must_change_password = True
        obj.failed_logins = 0
        obj.locked_until = None
        obj.save()
        AuthSession.objects.filter(user=obj, revoked_at__isnull=True).update(revoked_at=timezone.now(), revoke_reason="password_reset")
        audit.record("password_reset", "core.user", obj, reason=data.get("reason", ""))
        return obj

    def _grant(request, obj, data):
        _guard_target(request, obj)
        if not has_perm(request.user, "security.roles.manage"):
            raise BusinessRuleError("Role assignment requires security.roles.manage.", "not_authorised")
        role = Role.objects.filter(code=data.get("role"), deleted_at__isnull=True).first()
        if not role:
            raise BusinessRuleError("Unknown role.", "validation")
        if role.level == "owner" and not is_owner(request.user):
            raise BusinessRuleError("Only an owner can grant the owner role.", "not_authorised")
        if obj.pk == request.user.pk:
            raise BusinessRuleError("You cannot change your own roles.", "self_grant")
        dept = Department.objects.filter(pk=data.get("department")).first() if data.get("department") else None
        until = data.get("valid_until") or None
        grant, _ = UserRole.objects.update_or_create(
            user=obj, role=role, department=dept,
            defaults={"valid_until": until, "reason": data.get("reason", ""), "granted_by": request.user},
        )
        obj.bump_role_version()
        audit.record("role_granted", "core.user", obj, changes={"role": role.code, "department": str(dept.pk) if dept else None, "valid_until": until}, reason=data.get("reason", ""))
        return obj

    def _revoke(request, obj, data):
        _guard_target(request, obj)
        if not has_perm(request.user, "security.roles.manage"):
            raise BusinessRuleError("Role changes require security.roles.manage.", "not_authorised")
        if obj.pk == request.user.pk:
            raise BusinessRuleError("You cannot change your own roles.", "self_grant")
        deleted, _ = UserRole.objects.filter(user=obj, role__code=data.get("role")).delete()
        obj.bump_role_version()
        audit.record("role_revoked", "core.user", obj, changes={"role": data.get("role"), "grants_removed": deleted}, reason=data.get("reason", ""))
        return obj

    def _revoke_sessions(request, obj, data):
        _guard_target(request, obj)
        n = AuthSession.objects.filter(user=obj, revoked_at__isnull=True).update(revoked_at=timezone.now(), revoke_reason="admin_revoked")
        audit.record("sessions_revoked", "core.user", obj, changes={"count": n}, reason=data.get("reason", ""))
        return obj

    def _reset_mfa(request, obj, data):
        _guard_target(request, obj)
        obj.mfa_enabled = False
        obj.mfa_secret = ""
        obj.save(update_fields=["mfa_enabled", "mfa_secret"])
        AuthSession.objects.filter(user=obj, revoked_at__isnull=True).update(revoked_at=timezone.now(), revoke_reason="mfa_reset")
        audit.record("mfa_reset", "core.user", obj, reason=data.get("reason", ""))
        return obj

    def _unlock(request, obj, data):
        _guard_target(request, obj)
        obj.failed_logins = 0
        obj.locked_until = None
        obj.save(update_fields=["failed_logins", "locked_until"])
        audit.record("account_unlocked", "core.user", obj, reason=data.get("reason", ""))
        return obj

    actions = [
        Action("set_password", "Reset password", _set_password, permission=SEC_USERS, reason_required=True,
               fields=[{"name": "password", "label": "New temporary password", "type": "password", "required": True}]),
        Action("grant_role", "Grant role", _grant, permission="security.roles.manage", reason_required=True,
               fields=[{"name": "role", "label": "Role code", "type": "lookup", "source": "roles", "required": True},
                       {"name": "department", "label": "Department scope (optional)", "type": "fk", "target": "core.department"},
                       {"name": "valid_until", "label": "Valid until (delegation expiry)", "type": "datetime"}]),
        Action("revoke_role", "Revoke role", _revoke, permission="security.roles.manage", reason_required=True, tone="danger",
               fields=[{"name": "role", "label": "Role code", "type": "lookup", "source": "roles", "required": True}]),
        Action("revoke_sessions", "Sign out everywhere", _revoke_sessions, permission="security.sessions.manage", reason_required=True, tone="danger"),
        Action("reset_mfa", "Reset MFA", _reset_mfa, permission=SEC_USERS, reason_required=True, tone="danger"),
        Action("unlock", "Unlock account", _unlock, permission=SEC_USERS, reason_required=True,
               available=lambda o, r: bool(o.locked_until)),
    ]


@register
class SiteResource(Resource):
    key = "core.site"
    model = Site
    label = "Sites / plants"
    singular = "Site"
    module = "core"
    title_field = "name"
    permission_map = {"view": "core.department.view", "create": SEC_USERS, "update": SEC_USERS, "export": SEC_USERS}
    list_fields = ["code", "name", "address"]
    form_fields = ["name", "address"]
    create_only_fields = ["code"]
    search_fields = ["code", "name"]
    ordering = ["name"]

    def before_create(self, request, data):
        from .models import Company
        data["company"] = Company.objects.order_by("code").first()
        if data["company"] is None:
            raise BusinessRuleError("Run the installer first: no company is set up.", "invalid_state")


def default_site(request):
    return getattr(request.user, "site", None) or Site.objects.order_by("code").first()


@register
class DepartmentResource(Resource):
    key = "core.department"
    model = Department
    label = "Departments"
    singular = "Department"
    module = "core"
    title_field = "name"
    permission_map = {"create": SEC_USERS, "update": SEC_USERS, "export": SEC_USERS}
    list_fields = ["code", "name", "parent", "manager", "site", "active"]
    form_fields = ["name", "parent", "manager", "site", "active"]
    create_only_fields = ["code"]
    filter_fields = ["active", "site", "parent"]
    search_fields = ["code", "name"]
    ordering = ["name"]
    field_help = {
        "code": "Short code, e.g. STORE or PROD. It cannot be changed later.",
        "parent": "Optional: the larger department this team belongs to.",
        "manager": "Department head. Sees the department's tasks and approvals.",
        "site": "Leave empty for the main plant.",
        "active": "Untick instead of deleting: a department used in past records is kept for history.",
    }
    help = {
        "why": "Departments are picked on users, role grants (department scope), tasks, gate staff and inward material.",
        "steps": "Add a department here, then choose it on a user (Administration → Users) or when granting a role.",
    }

    def before_create(self, request, data):
        data["code"] = str(data.get("code") or "").strip().upper()
        if not data.get("site"):
            data["site"] = default_site(request)
            if data["site"] is None:
                raise BusinessRuleError("Add a site first (Administration → Sites / plants).", "validation")

    def extra_detail(self, request, obj):
        return {"members": obj.members.filter(is_active=True).count()}


@register
class RoleResource(Resource):
    key = "core.role"
    model = Role
    label = "Roles"
    singular = "Role"
    module = "core"
    title_field = "name"
    permission_map = {"view": "security.roles.manage", "create": "security.roles.manage", "update": "security.roles.manage", "export": "security.roles.manage"}
    list_fields = ["name", "code", "level", "is_system", "customized"]
    form_fields = ["name", "description"]
    create_only_fields = ["code", "level"]
    readonly_fields = ["is_system", "customized"]
    ordering = ["level", "name"]
    help = {
        "steps": "Open a role and tick View / Add / Edit / Delete / Approve / Export for each module or record type, then save with a reason.",
        "boundary": "The owner role always has full access and cannot be changed or deleted. The owner or an administrator can delete other roles, "
                    "and only once no user holds them. You cannot grant permissions you do not hold yourself.",
    }

    def scope(self, request, qs):
        return qs.filter(deleted_at__isnull=True)

    def before_create(self, request, data):
        if data.get("level") == "owner" and not is_owner(request.user):
            raise BusinessRuleError("Only an owner can create an owner-level role.", "privilege_escalation")
        if Role.objects.filter(code=data.get("code"), deleted_at__isnull=False).exists():
            raise BusinessRuleError("That code belonged to a deleted built-in role. Choose another code.", "validation")

    def after_update(self, request, obj, changes):
        if changes and obj.is_system and not obj.customized:
            obj.customized = True
            obj.save(update_fields=["customized"])

    def edit_block_reason(self, request, obj):
        if obj.code == "owner":
            return "The owner role always has full access; it cannot be edited."
        return None

    def delete_block_reason(self, request, obj):
        if obj.code == "owner" or obj.level == "owner":
            return "The owner role cannot be deleted."
        return None

    def before_delete(self, request, obj):
        holders = list(User.objects.filter(role_grants__role=obj).distinct().order_by("full_name").values_list("full_name", flat=True))
        if holders:
            names = ", ".join(holders[:10]) + (f" and {len(holders) - 10} more" if len(holders) > 10 else "")
            raise BusinessRuleError(f"{len(holders)} user(s) still have this role: {names}. Revoke it from them first (Users → Revoke role).",
                                    "role_in_use", {"users": holders})

    def destroy(self, request, obj):
        if obj.is_system:
            # Kept as a hidden row so upgrades do not recreate it.
            obj.permissions.clear()
            obj.deleted_at = timezone.now()
            obj.save(update_fields=["deleted_at"])
        else:
            obj.delete()

    def extra_detail(self, request, obj):
        from .rbac import BASELINE_PERMISSIONS
        return {
            "permissions": sorted(obj.permissions.values_list("code", flat=True)),
            "users": obj.grants.count(),
            "is_owner_role": obj.code == "owner",
            "baseline": [] if obj.level == "viewer" else BASELINE_PERMISSIONS,
            "can_grant": None if is_owner(request.user) else sorted(effective_permissions(request.user)),
        }

    def _set_permissions(request, obj, data):
        from .models import Permission, User
        from .rbac import BASELINE_PERMISSIONS
        if obj.code == "owner":
            raise BusinessRuleError("The owner role always has full access; it cannot be changed.", "invalid_state")
        codes = set(data.get("permissions") or [])
        unknown = codes - set(Permission.objects.filter(code__in=codes).values_list("code", flat=True))
        if unknown:
            raise BusinessRuleError(f"Unknown permissions: {', '.join(sorted(unknown))}", "validation")
        baseline = set(Permission.objects.filter(code__in=BASELINE_PERMISSIONS).values_list("code", flat=True))
        if obj.level != "viewer":
            codes |= baseline  # every named user needs these (people lookup, own inbox)
        current = set(obj.permissions.values_list("code", flat=True))
        if not is_owner(request.user):
            mine = effective_permissions(request.user)
            added = codes - current - baseline
            if any(c not in mine for c in added):
                raise BusinessRuleError("You cannot grant permissions you do not hold.", "privilege_escalation")
        obj.permissions.set(Permission.objects.filter(code__in=codes))
        if obj.is_system and not obj.customized:
            obj.customized = True
            obj.save(update_fields=["customized"])
        User.objects.filter(role_grants__role=obj).distinct().update(role_version=models_F("role_version") + 1)
        audit.record("role_permissions_set", "core.role", obj,
                     changes={"added": sorted(codes - current), "removed": sorted(current - codes)}, reason=data.get("reason", ""))
        return obj

    def _reset_default(request, obj, data):
        from .models import Permission, User
        from .rbac import default_role_permissions
        if not obj.is_system or obj.code == "owner":
            raise BusinessRuleError("Only built-in roles have a default to return to.", "invalid_state")
        before = set(obj.permissions.values_list("code", flat=True))
        codes = default_role_permissions(obj.code)
        obj.permissions.set(Permission.objects.filter(code__in=codes))
        obj.customized = False
        obj.save(update_fields=["customized"])
        User.objects.filter(role_grants__role=obj).distinct().update(role_version=models_F("role_version") + 1)
        audit.record("role_reset_default", "core.role", obj,
                     changes={"added": sorted(codes - before), "removed": sorted(before - codes)}, reason=data.get("reason", ""))
        return obj

    actions = [
        Action("set_permissions", "Save permissions", _set_permissions, permission="security.roles.manage", reason_required=True,
               available=lambda o, r: o.code != "owner",
               fields=[{"name": "permissions", "label": "Permission codes", "type": "multiselect", "source": "permissions"}]),
        Action("reset_default", "Reset to built-in default", _reset_default, permission="security.roles.manage", reason_required=True,
               tone="danger", confirm="Replace this role's permissions with the original built-in set?",
               available=lambda o, r: o.is_system and o.customized and o.code != "owner"),
    ]


def models_F(name):
    from django.db.models import F
    return F(name)


@register
class DeviceResource(Resource):
    key = "core.device"
    model = Device
    label = "Enrolled devices"
    singular = "Device"
    module = "core"
    title_field = "name"
    permission_map = {"view": "security.devices.manage", "create": "security.devices.manage", "update": "security.devices.manage", "export": "security.devices.manage"}
    list_fields = ["name", "kind", "site", "status", "enrolled_at", "last_seen", "last_ip"]
    form_fields = ["name", "kind", "site"]
    readonly_fields = ["enrolled_ip", "revoked_at", "revoked_reason", "enrollment_expires"]
    filter_fields = ["status", "kind"]
    search_fields = ["name"]
    help = {
        "why": "Zero trust: an approved network alone does not prove a trusted device. Each PC/tablet/gate/TV is enrolled once.",
        "steps": "Create the device, read the one-time code on the device's enrolment screen within 24 hours.",
        "boundary": "Revoking a device ends every session on it immediately.",
    }

    def create(self, request, data):
        code, code_hash = new_enrollment_code()
        device = Device.objects.create(
            enrollment_code_hash=code_hash, enrollment_expires=timezone.now() + timedelta(hours=24), created_by=request.user, **data,
        )
        request._enrollment_code = code
        return device

    def extra_detail(self, request, obj):
        code = getattr(request, "_enrollment_code", None)
        return {"enrollment_code": code} if code else {}

    def _new_code(request, obj, data):
        if obj.status == "revoked":
            raise BusinessRuleError("Revoked devices cannot be re-enrolled; create a new device.", "invalid_state")
        code, code_hash = new_enrollment_code()
        obj.enrollment_code_hash = code_hash
        obj.enrollment_expires = timezone.now() + timedelta(hours=24)
        obj.status = "pending"
        obj.secret_hash = ""
        obj.save()
        AuthSession.objects.filter(device=obj, revoked_at__isnull=True).update(revoked_at=timezone.now(), revoke_reason="device_reenrolled")
        audit.record("device_code_issued", "core.device", obj, reason=data.get("reason", ""))
        return {"enrollment_code": code, "expires": obj.enrollment_expires}

    def _revoke(request, obj, data):
        obj.status = "revoked"
        obj.secret_hash = ""
        obj.revoked_at = timezone.now()
        obj.revoked_reason = data.get("reason", "")
        obj.save()
        n = AuthSession.objects.filter(device=obj, revoked_at__isnull=True).update(revoked_at=timezone.now(), revoke_reason="device_revoked")
        audit.record("device_revoked", "core.device", obj, changes={"sessions_revoked": n}, reason=data.get("reason", ""))
        return obj

    actions = [
        Action("new_code", "Issue enrolment code", _new_code, permission="security.devices.manage", reason_required=True),
        Action("revoke", "Revoke device", _revoke, permission="security.devices.manage", reason_required=True, tone="danger",
               available=lambda o, r: o.status != "revoked"),
    ]



@register
class SessionResource(Resource):
    key = "core.authsession"
    model = AuthSession
    label = "Sessions"
    singular = "Session"
    module = "core"
    title_field = "id"
    permission_map = {"view": "security.sessions.manage", "export": "security.sessions.manage"}
    allow_create = False
    allow_update = False
    list_fields = ["user", "device", "ip", "mfa_verified", "created_at", "last_seen", "expires_at", "revoked_at", "revoke_reason"]
    filter_fields = ["user", "device"]
    ordering = ["-created_at"]

    def _revoke(request, obj, data):
        obj.revoked_at = timezone.now()
        obj.revoke_reason = "admin_revoked"
        obj.save(update_fields=["revoked_at", "revoke_reason"])
        audit.record("session_revoked", "core.authsession", obj, reason=data.get("reason", ""))
        return obj

    actions = [Action("revoke", "Revoke", _revoke, permission="security.sessions.manage", reason_required=True, tone="danger",
                      available=lambda o, r: o.revoked_at is None)]


@register
class ApprovalResource(Resource):
    """Maker / checker queue. Checkers see requests they are authorised to decide; makers see their own."""

    key = "core.approvalrequest"
    model = ApprovalRequest
    label = "Approvals (maker / checker)"
    singular = "Approval request"
    module = "core"
    title_field = "summary"
    allow_create = False
    allow_update = False
    list_fields = ["summary", "kind", "status", "created_by", "created_at", "required_approvals", "amount", "expires_at", "decided_by"]
    readonly_fields = ["resource", "operation", "action", "object_id", "doc_no", "reason", "changes", "approvals", "required_permission",
                       "decision_note", "executed_at", "result", "policy"]
    filter_fields = ["status", "kind", "created_by", "resource"]
    search_fields = ["summary", "doc_no", "reason"]
    field_permissions = {"amount": "commercial.price.view"}
    help = {
        "why": "Four-eyes control: the person who makes a sensitive change can never be the one who approves it.",
        "steps": "Maker submits -> request held (nothing changes) -> authorised checker(s) approve or reject -> change applied in the maker's name.",
        "boundary": "If the record changed after submission the request becomes 'stale' and must be resubmitted. The maker's permission is re-checked at approval time.",
    }

    def scope(self, request, qs):
        if is_owner(request.user):
            return qs
        perms = list(effective_permissions(request.user))
        return qs.filter(Q(created_by=request.user) | Q(required_permission__in=perms))

    def extra_detail(self, request, obj):
        hidden = set()
        from .registry import REGISTRY
        res = REGISTRY.get(obj.resource)
        if res:
            hidden = {f for f, p in res.field_permissions.items() if not has_perm(request.user, p)}
        return {
            "proposed_changes": {k: v for k, v in (obj.changes or {}).items() if k not in hidden},
            "progress": f"{len(obj.approvals)}/{obj.required_approvals} approvals",
            "checkers": obj.approvals,
            "you_can_decide": obj.status == "pending" and obj.created_by_id != request.user.pk and has_perm(request.user, obj.required_permission)
                              and str(request.user.pk) not in {a.get("user") for a in obj.approvals},
            "you_are_maker": obj.created_by_id == request.user.pk,
            "target": {"resource": obj.resource, "id": obj.object_id} if obj.object_id else None,
        }

    def _can_decide(o, r):
        return (o.status == "pending" and o.created_by_id != r.user.pk and has_perm(r.user, o.required_permission)
                and str(r.user.pk) not in {a.get("user") for a in o.approvals})

    def _approve(request, obj, data):
        from .maker_checker import decide
        return decide(obj.pk, request.user, True, data.get("note", ""))

    def _reject(request, obj, data):
        from .maker_checker import decide
        return decide(obj.pk, request.user, False, data.get("reason", ""))

    def _withdraw(request, obj, data):
        from .maker_checker import withdraw
        return withdraw(obj.pk, request.user, data.get("reason", ""))

    actions = [
        Action("approve", "Approve", _approve, permission="core.approvalrequest.view", tone="primary",
               fields=[{"name": "note", "label": "Checker note", "type": "textarea"}], available=_can_decide),
        Action("reject", "Reject", _reject, permission="core.approvalrequest.view", reason_required=True, tone="danger", available=_can_decide),
        Action("withdraw", "Withdraw my request", _withdraw, permission="core.approvalrequest.view", reason_required=True,
               available=lambda o, r: o.status == "pending" and o.created_by_id == r.user.pk),
    ]


@register
class OutboxResource(Resource):
    key = "core.outboxmessage"
    model = OutboxMessage
    label = "Message outbox"
    singular = "Message"
    module = "core"
    title_field = "template"
    permission_map = {"view": "outbox.approve", "export": "outbox.approve"}
    allow_create = False
    allow_update = False
    list_fields = ["channel", "template", "recipient", "status", "source_resource", "source_version", "attempts", "last_error", "created_at"]
    readonly_fields = ["payload", "dedupe_key", "provider_id", "approved_by"]
    filter_fields = ["status", "channel", "source_resource"]
    help = {
        "states": "Draft (approval needed) -> Approved -> Queued -> Provider accepted -> Delivered -> Read. Failed, Suppressed and Cancelled keep their reason.",
        "boundary": "Delivered or Read confirms the message only - the work stays open until its own workflow verifies it. "
                    "Editing the source document suppresses unsent drafts so old quantities/dates are never sent.",
    }

    def _approve(request, obj, data):
        if obj.status != "draft":
            raise BusinessRuleError("Only drafts can be approved.", "invalid_state")
        obj.status = "approved"
        obj.approved_by = request.user
        obj.save()
        audit.record("outbox_approved", "core.outboxmessage", obj)
        return obj

    def _cancel(request, obj, data):
        obj.status = "cancelled"
        obj.last_error = data.get("reason", "")
        obj.save()
        audit.record("outbox_cancelled", "core.outboxmessage", obj, reason=data.get("reason", ""))
        return obj

    actions = [
        Action("approve", "Approve for sending", _approve, permission="outbox.approve", tone="primary", available=lambda o, r: o.status == "draft"),
        Action("cancel", "Cancel", _cancel, permission="outbox.approve", reason_required=True, tone="danger", available=lambda o, r: o.status in {"draft", "approved", "queued", "failed"}),
    ]
