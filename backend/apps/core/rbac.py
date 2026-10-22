"""
Role-based access control with deny-by-default enforcement (brief p.27, OWASP R1).

* Permissions are checked on the server for every request, export, attachment
  and action. Hiding a menu item in the UI is never the control.
* Effective permissions are recomputed per request from active, time-boxed
  role grants, so revocation and delegation expiry take effect immediately.
"""
import logging
from fnmatch import fnmatch

from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

security_log = logging.getLogger("kantam.security")

from .models import Permission, Role, UserRole


def active_grants(user):
    now = timezone.now()
    return (
        UserRole.objects.filter(user=user, valid_from__lte=now)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))
        .select_related("role", "department")
    )


def effective_permissions(user):
    if user is None or not getattr(user, "is_active", False):
        return frozenset()
    cached = getattr(user, "_kantam_perms", None)
    if cached is not None:
        return cached
    role_ids = [g.role_id for g in active_grants(user)]
    codes = frozenset(
        Permission.objects.filter(roles__id__in=role_ids).values_list("code", flat=True).distinct()
    )
    user._kantam_perms = codes
    return codes


def role_levels(user):
    cached = getattr(user, "_kantam_levels", None)
    if cached is not None:
        return cached
    levels = frozenset(g.role.level for g in active_grants(user))
    user._kantam_levels = levels
    return levels


def role_codes(user):
    return sorted({g.role.code for g in active_grants(user)})


def has_perm(user, code):
    return code in effective_permissions(user)


def has_any(user, *codes):
    perms = effective_permissions(user)
    return any(c in perms for c in codes)


def is_owner(user):
    return "owner" in role_levels(user)


def scoped_department_ids(user):
    """Departments the user may see department-scoped records for."""
    ids = {g.department_id for g in active_grants(user) if g.department_id}
    if user.department_id:
        ids.add(user.department_id)
    return ids


BASELINE_PERMISSIONS = [
    "core.userlookup.view", "core.department.view", "core.approvalrequest.view",
    # Personal inboxes are row-scoped to the signed-in user.
    "gate.myvisit.view", "gate.mycourier.view", "tasks.task.view", "tasks.task.update",
]


def expand_patterns(include, exclude, all_codes):
    granted = {c for c in all_codes if any(fnmatch(c, p) for p in include)}
    return {c for c in granted if not any(fnmatch(c, p) for p in exclude)}


class DenyByDefault(BasePermission):
    """
    A view is accessible only if it declares ``required_permission`` (str),
    ``required_permissions`` (dict action/method -> str), or implements
    ``get_required_permission(request)``. Anything undeclared is denied.
    """

    message = "Access denied."

    def has_permission(self, request, view):
        if getattr(view, "public_endpoint", False):
            return True
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        needed = None
        if hasattr(view, "get_required_permission"):
            needed = view.get_required_permission(request)
        elif hasattr(view, "required_permissions"):
            key = getattr(view, "action", None) or request.method.lower()
            needed = view.required_permissions.get(key) or view.required_permissions.get(request.method.lower())
        elif hasattr(view, "required_permission"):
            needed = view.required_permission
        # OWASP ASVS 2.1 / 3.x: a temporary (admin-set) password must be changed before any business use.
        if getattr(user, "must_change_password", False) and not getattr(view, "allow_during_password_change", False):
            raise PermissionDenied({"error": "password_change_required", "message": "Change your temporary password to continue."})
        if needed is None:
            granted = False
        elif needed == "authenticated":
            granted = True
        elif isinstance(needed, (list, tuple, set)):
            granted = has_any(user, *needed)
        else:
            granted = has_perm(user, needed)
        if not granted:
            # OWASP A09: record access-control failures for monitoring (no request bodies or tokens logged).
            security_log.warning("access_denied user=%s path=%s method=%s needed=%s ip=%s", getattr(user, "username", "?"), request.path,
                                 request.method, needed, getattr(request, "zt_client_ip", None))
        return granted


def sync_roles_from_matrix():
    """Create/refresh system roles from rbac_matrix against current permission set."""
    from .rbac_matrix import ROLES

    all_codes = set(Permission.objects.values_list("code", flat=True))
    for code, spec in ROLES.items():
        role, _ = Role.objects.update_or_create(
            code=code,
            defaults={"name": spec["name"], "level": spec["level"], "description": spec["description"], "is_system": True},
        )
        granted = expand_patterns(spec["include"], spec["exclude"], all_codes)
        if spec["level"] != "viewer":
            # Minimal shared projection every named user needs (name-only people
            # lookup for assignment fields, department list, approval inbox).
            granted |= {c for c in BASELINE_PERMISSIONS if c in all_codes}
        role.permissions.set(Permission.objects.filter(code__in=granted))
    # Any change to role definitions invalidates outstanding tokens.
    from .models import User
    from django.db.models import F
    User.objects.update(role_version=F("role_version") + 1)
