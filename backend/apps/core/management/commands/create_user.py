import getpass
import os

from django.contrib.auth import password_validation
from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Department, Role, User, UserRole


class Command(BaseCommand):
    help = "Create a named user with one or more roles. Password is prompted (never a default)."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--name", required=True)
        parser.add_argument("--role", action="append", required=True, help="Role code (repeatable). See rbac_matrix.py")
        parser.add_argument("--department", help="Department code")
        parser.add_argument("--password-env", help="Read password from this environment variable")

    def handle(self, *args, **o):
        if User.objects.filter(username=o["username"].lower()).exists():
            raise CommandError("User exists.")
        dept = Department.objects.filter(code=o["department"]).first() if o.get("department") else None
        roles = [Role.objects.filter(code=c).first() for c in o["role"]]
        if not all(roles):
            raise CommandError(f"Unknown role in {o['role']}. Valid: {', '.join(Role.objects.values_list('code', flat=True))}")
        pwd = os.environ.get(o["password_env"], "") if o.get("password_env") else getpass.getpass("Password: ")
        user = User(username=o["username"].lower(), full_name=o["name"], department=dept, site=dept.site if dept else None)
        password_validation.validate_password(pwd, user)
        user.set_password(pwd)
        # The administrator chose this password, so it is temporary: the person sets their own at first sign-in.
        user.must_change_password = True
        user.save()
        for r in roles:
            UserRole.objects.create(user=user, role=r, reason="created by command")
        self.stdout.write(self.style.SUCCESS(f"Created {user.username} with roles {', '.join(o['role'])}"))
