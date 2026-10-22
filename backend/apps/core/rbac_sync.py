from .models import Permission
from .rbac import sync_roles_from_matrix
from .registry import all_permission_codes


def sync_permissions():
    codes = all_permission_codes()
    for code, (module, desc) in codes.items():
        Permission.objects.update_or_create(code=code, defaults={"module": module, "description": desc[:200]})
    Permission.objects.exclude(code__in=list(codes)).delete()
    sync_roles_from_matrix()
    from .maker_checker import seed_default_policies
    seed_default_policies()
    return len(codes)
