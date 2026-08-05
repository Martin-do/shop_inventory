from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0011_rename_auditlog_index"),
        ("inventory", "0012_granular_staff_permissions"),
    ]

    operations = []
