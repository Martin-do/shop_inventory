from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0017_full_pack_workflow"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StocktakeRemovedCount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("original_count_id", models.PositiveIntegerField()),
                ("snapshot", models.JSONField(default=dict)),
                ("reason", models.CharField(max_length=240)),
                ("removed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("restored_at", models.DateTimeField(blank=True, null=True)),
                ("restored_count_id", models.PositiveIntegerField(blank=True, null=True)),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="removed_stocktake_counts", to="inventory.product")),
                ("removed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="removed_stocktake_counts", to=settings.AUTH_USER_MODEL)),
                ("restored_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="restored_stocktake_counts", to=settings.AUTH_USER_MODEL)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="removed_counts", to="inventory.stocktakesession")),
                ("zone", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="removed_counts", to="inventory.stocktakezone")),
            ],
            options={"ordering": ["-removed_at"]},
        ),
    ]
