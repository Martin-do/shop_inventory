from django.db import migrations, models
import django.core.validators


def backfill_sale_quantities(apps, schema_editor):
    SaleItem = apps.get_model("inventory", "SaleItem")
    for item in SaleItem.objects.all().iterator():
        item.sale_quantity = item.quantity
        item.save(update_fields=["sale_quantity"])


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0016_stocktake_pack_counting"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="pack_selling_price",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Optional. Price for one full pack; defaults to unit price multiplied by units per pack.",
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.AddField(
            model_name="stockreceiptline",
            name="count_entry_mode",
            field=models.CharField(
                choices=[("units", "Total units"), ("packs", "Packs + loose units")],
                default="units",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="stockreceiptline",
            name="pack_count_entered",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="stockreceiptline",
            name="loose_units_entered",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="stockreceiptline",
            name="units_per_pack_snapshot",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="saleitem",
            name="sale_unit",
            field=models.CharField(
                choices=[("unit", "Unit"), ("pack", "Pack")],
                default="unit",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="saleitem",
            name="sale_quantity",
            field=models.PositiveIntegerField(
                default=1,
                validators=[django.core.validators.MinValueValidator(1)],
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="saleitem",
            name="units_per_pack_snapshot",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_sale_quantities, migrations.RunPython.noop),
    ]
