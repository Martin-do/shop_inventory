from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0015_stock_receipts_and_safe_product_delete"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="units_per_pack",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Optional. Number of individual sellable units in one full pack.",
                null=True,
                validators=[django.core.validators.MinValueValidator(2)],
            ),
        ),
        migrations.AddField(
            model_name="stocktakecount",
            name="count_entry_mode",
            field=models.CharField(
                choices=[("units", "Total units"), ("packs", "Packs + loose units")],
                default="units",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="stocktakecount",
            name="pack_count_entered",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="stocktakecount",
            name="loose_units_entered",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="stocktakecount",
            name="units_per_pack_snapshot",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
