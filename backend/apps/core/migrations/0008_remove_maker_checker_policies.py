from django.db import migrations
from django.utils import timezone


def switch_off(apps, schema_editor):
    """Maker / checker policies are removed: turn them off and close requests still waiting on one."""
    ApprovalPolicy = apps.get_model("core", "ApprovalPolicy")
    ApprovalRequest = apps.get_model("core", "ApprovalRequest")
    ApprovalPolicy.objects.update(active=False)
    ApprovalRequest.objects.filter(kind="policy", status="pending").update(
        status="withdrawn", decision_note="Maker / checker approvals were removed; submit the change again (it now applies directly).",
        updated_at=timezone.now(),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_role_deleted_department_site"),
    ]

    operations = [migrations.RunPython(switch_off, migrations.RunPython.noop)]
