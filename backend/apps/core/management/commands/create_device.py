from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.auth_views import new_enrollment_code
from apps.core.models import Device


class Command(BaseCommand):
    help = "Register a device and print its one-time enrolment code (valid 24 hours)."

    def add_arguments(self, parser):
        parser.add_argument("name")
        parser.add_argument("--kind", default="pc", choices=[k for k, _ in Device.KINDS])

    def handle(self, *args, **o):
        code, code_hash = new_enrollment_code()
        Device.objects.create(name=o["name"], kind=o["kind"], enrollment_code_hash=code_hash, enrollment_expires=timezone.now() + timedelta(hours=24))
        self.stdout.write(self.style.WARNING(f"Enrolment code for '{o['name']}': {code}"))
