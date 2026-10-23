import re
from decimal import Decimal

from django.db import migrations, models


def copy_dimensions(apps, schema_editor):
    """Old free-text sizes like '98.25 x 506' or 'H 98.25mm W 506mm' -> height / width in mm (first number is height)."""
    Artwork = apps.get_model("masters", "Artwork")
    for art in Artwork.objects.exclude(dimensions=""):
        numbers = re.findall(r"\d+(?:\.\d+)?", art.dimensions)
        if len(numbers) >= 2:
            art.height_mm, art.width_mm = Decimal(numbers[0]), Decimal(numbers[1])
            art.save(update_fields=["height_mm", "width_mm"])


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0002_remove_contact_email_remove_contact_email_verified_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="artwork",
            name="height_mm",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True, verbose_name="Height (mm)"),
        ),
        migrations.AddField(
            model_name="artwork",
            name="width_mm",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True, verbose_name="Width (mm)"),
        ),
        migrations.RunPython(copy_dimensions, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="artwork",
            name="dimensions",
        ),
    ]
