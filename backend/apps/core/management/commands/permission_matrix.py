from pathlib import Path

from django.core.management.base import BaseCommand

from apps.core.models import Role
from apps.core.registry import MODULES


class Command(BaseCommand):
    help = "Write docs/permission-matrix.md from the live role definitions."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(Path(__file__).resolve().parents[5] / "docs" / "permission-matrix.md"))

    def handle(self, *args, **o):
        roles = list(Role.objects.order_by("level", "code").prefetch_related("permissions"))
        lines = ["# Permission matrix", "", "Generated from `backend/apps/core/rbac_matrix.py` by `manage.py permission_matrix`.",
                 "Deny by default: anything not listed is refused on the server (API, exports, attachments, actions).", ""]
        lines += ["## Roles", "", "| Role | Level | Description | # permissions |", "|---|---|---|---|"]
        for r in roles:
            lines.append(f"| `{r.code}` {r.name} | {r.level} | {r.description} | {r.permissions.count()} |")
        lines.append("")
        for code, label in MODULES + [("commercial", "Commercial projections"), ("security", "Security"), ("hr", "HR"), ("finance", "Finance")]:
            mod_perms = sorted({p.code for r in roles for p in r.permissions.all() if p.code.split(".")[0] in {code} or p.module == code})
            if not mod_perms:
                continue
            lines += [f"## {label}", "", "| Permission | " + " | ".join(r.code for r in roles) + " |", "|---|" + "---|" * len(roles)]
            for perm in mod_perms:
                lines.append(f"| `{perm}` | " + " | ".join("✔" if r.permissions.filter(code=perm).exists() else "" for r in roles) + " |")
            lines.append("")
        Path(o["out"]).write_text("\n".join(lines))
        self.stdout.write(self.style.SUCCESS(f"Wrote {o['out']}"))
