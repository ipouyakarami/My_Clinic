from django.db import migrations, models


def clamp_negative_inventory(apps, schema_editor):
    Medication = apps.get_model("medications", "Medication")
    Medication.objects.filter(current_inventory__lt=0).update(current_inventory=0)


class Migration(migrations.Migration):

    dependencies = [
        ("medications", "0005_telegramactivationcode"),
    ]

    operations = [
        migrations.RunPython(clamp_negative_inventory, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="medication",
            constraint=models.CheckConstraint(
                condition=models.Q(("current_inventory__gte", 0)),
                name="medication_current_inventory_non_negative",
            ),
        ),
    ]