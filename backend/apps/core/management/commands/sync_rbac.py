from django.core.management.base import BaseCommand

from apps.core.rbac_sync import sync_permissions


class Command(BaseCommand):
    help = "Sync permission codes from the resource registry and apply rbac_matrix.py to system roles."

    def handle(self, *args, **opts):
        n = sync_permissions()
        self.stdout.write(self.style.SUCCESS(f"{n} permissions synced; system roles refreshed; all tokens invalidated."))
