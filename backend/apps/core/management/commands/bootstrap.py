"""
Idempotent first-run setup: company, site, departments, store locations, reason
codes and the first owner account + an enrolment code for the owner's PC.
There are no default passwords: the owner password is typed at the terminal
(or supplied via an environment variable for automated installs).
"""
import getpass
import os
from datetime import timedelta

from django.contrib.auth import password_validation
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.auth_views import new_enrollment_code
from apps.core.models import Company, Department, Device, Role, Site, User, UserRole
from apps.core.rbac_sync import sync_permissions
from apps.masters.models import Location, Reason

DEPARTMENTS = [
    ("MGMT", "Management"), ("SALES", "Sales & marketing"), ("ART", "Artwork & labels"), ("PLAN", "Planning"),
    ("PROD", "Production"), ("STORE", "Stores"), ("PUR", "Purchase"), ("QC", "Quality"), ("DISP", "FG & dispatch"),
    ("SEC", "Security"), ("HR", "HR / workforce"), ("MAINT", "Maintenance"), ("ACC", "Accounts"), ("IT", "IT / security admin"),
]
LOCATIONS = [
    ("RM-01", "Raw material store", "warehouse"), ("LBL-01", "Label store", "label_store"), ("PKG-01", "Packing material store", "warehouse"),
    ("FG-01", "FG / SFG store", "fg"), ("QCH-01", "QC hold area", "qc_hold"), ("DSP-01", "Dispatch staging", "dispatch"),
    ("SCR-01", "Scrap / regrind", "scrap"), ("V-SUPPLIER", "Supplier (virtual)", "supplier"), ("V-CUSTOMER", "Customer (virtual)", "customer"),
    ("V-PRODUCTION", "Production consumption (virtual)", "production"), ("V-ADJUSTMENT", "Adjustment (virtual)", "adjustment"),
]
REASONS = [
    ("downtime", "DT01", "Mould change / setup"), ("downtime", "DT02", "Machine breakdown"), ("downtime", "DT03", "Robot / IML fault"),
    ("downtime", "DT04", "Material shortage"), ("downtime", "DT05", "Label shortage"), ("downtime", "DT06", "Power failure"),
    ("downtime", "DT07", "No operator"), ("downtime", "DT08", "Quality hold"), ("downtime", "DT09", "Planned idle / no plan"),
    ("rejection", "RJ01", "Short shot"), ("rejection", "RJ02", "Flash"), ("rejection", "RJ03", "Label misalignment"), ("rejection", "RJ04", "Label wrinkle / join"),
    ("rejection", "RJ05", "Warpage / lid fit"), ("rejection", "RJ06", "Black spot / contamination"), ("rejection", "RJ07", "Weight out of tolerance"),
    ("hold", "QH01", "Awaiting final inspection"), ("hold", "QH02", "Customer complaint containment"),
    ("cancellation", "CN01", "Customer cancellation"), ("cancellation", "CN02", "Artwork change"),
]


class Command(BaseCommand):
    help = "First-run setup (idempotent). Creates the owner account and an enrolment code for the owner's device."

    def add_arguments(self, parser):
        parser.add_argument("--owner", default="owner", help="Owner username")
        parser.add_argument("--owner-name", default="Owner")
        parser.add_argument("--password-env", help="Read owner password from this environment variable instead of prompting")
        parser.add_argument("--device-name", default="Owner PC")

    @transaction.atomic
    def handle(self, *args, **o):
        sync_permissions()
        company, _ = Company.objects.get_or_create(code="KANTAM", defaults={"name": "Kantam Kontainers LLP", "address": "Bareja, Ahmedabad, Gujarat"})
        site, _ = Site.objects.get_or_create(code="BAREJA", defaults={"company": company, "name": "Bareja plant"})
        for code, name in DEPARTMENTS:
            Department.objects.get_or_create(code=code, defaults={"site": site, "name": name})
        for code, name, kind in LOCATIONS:
            Location.objects.get_or_create(code=code, defaults={"name": name, "kind": kind})
        for kind, code, text in REASONS:
            Reason.objects.get_or_create(kind=kind, code=code, defaults={"text": text})

        username = o["owner"].lower()
        user = User.objects.filter(username=username).first()
        if user is None:
            if o["password_env"]:
                pwd = os.environ.get(o["password_env"], "")
            else:
                pwd = getpass.getpass(f"Password for owner '{username}': ")
                if pwd != getpass.getpass("Repeat password: "):
                    raise CommandError("Passwords do not match.")
            user = User(username=username, full_name=o["owner_name"], site=site, department=Department.objects.get(code="MGMT"))
            password_validation.validate_password(pwd, user)
            user.set_password(pwd)
            user.save()
            UserRole.objects.create(user=user, role=Role.objects.get(code="owner"), reason="bootstrap")
            self.stdout.write(self.style.SUCCESS(f"Owner '{username}' created. MFA (TOTP) enrolment is required at first sign-in."))
        else:
            self.stdout.write(f"Owner '{username}' already exists - unchanged.")

        code, code_hash = new_enrollment_code()
        Device.objects.create(name=o["device_name"], kind="pc", site=site, enrollment_code_hash=code_hash,
                              enrollment_expires=timezone.now() + timedelta(hours=24), created_by=user)
        self.stdout.write(self.style.WARNING(f"Device enrolment code for '{o['device_name']}' (valid 24h, shown once): {code}"))
