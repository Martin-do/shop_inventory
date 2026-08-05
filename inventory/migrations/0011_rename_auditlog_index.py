from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0010_opening_stocktake"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="auditlog",
            old_name="inventory_a_object__b3e659_idx",
            new_name="inventory_a_object__6617b5_idx",
        ),
    ]
