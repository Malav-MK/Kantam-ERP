from django.db.models.signals import post_migrate
from django.dispatch import receiver


@receiver(post_migrate)
def sync_rbac_after_migrate(sender, **kwargs):
    # post_migrate fires after the whole migrate run; act once (on the core app).
    if getattr(sender, "name", "") != "apps.core":
        return
    from .rbac_sync import sync_permissions

    sync_permissions()
