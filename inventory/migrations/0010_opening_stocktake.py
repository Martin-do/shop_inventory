import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0009_audit_history_and_staff_attribution"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StocktakeSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=180)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("counting", "Counting"), ("review", "Under review"), ("applied", "Applied"), ("cancelled", "Cancelled")], db_index=True, default="draft", max_length=20)),
                ("blind_count", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("applied_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="applied_stocktakes", to=settings.AUTH_USER_MODEL)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_stocktakes", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="StocktakeZone",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("description", models.CharField(blank=True, max_length=240)),
                ("is_complete", models.BooleanField(default=False)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("assigned_users", models.ManyToManyField(blank=True, related_name="stocktake_zones", to=settings.AUTH_USER_MODEL)),
                ("completed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="completed_stocktake_zones", to=settings.AUTH_USER_MODEL)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="zones", to="inventory.stocktakesession")),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="StocktakeCount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("good_quantity", models.PositiveIntegerField(default=0)),
                ("damaged_quantity", models.PositiveIntegerField(default=0)),
                ("expired_quantity", models.PositiveIntegerField(default=0)),
                ("reserved_quantity", models.PositiveIntegerField(default=0)),
                ("approved_good_quantity", models.PositiveIntegerField(default=0)),
                ("first_count_quantity", models.PositiveIntegerField(default=0)),
                ("recount_quantity", models.PositiveIntegerField(blank=True, null=True)),
                ("status", models.CharField(choices=[("counted", "Counted"), ("recount", "Recount required"), ("approved", "Approved")], default="counted", max_length=20)),
                ("note", models.CharField(blank=True, max_length=240)),
                ("counted_at", models.DateTimeField(auto_now=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="approved_stocktake_counts", to=settings.AUTH_USER_MODEL)),
                ("counted_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="stocktake_counts", to=settings.AUTH_USER_MODEL)),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="stocktake_counts", to="inventory.product")),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="counts", to="inventory.stocktakesession")),
                ("zone", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="counts", to="inventory.stocktakezone")),
            ],
            options={"ordering": ["zone__name", "product__name"]},
        ),
        migrations.CreateModel(
            name="StocktakeEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event", models.CharField(max_length=80)),
                ("message", models.CharField(max_length=240)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="inventory.stocktakesession")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(model_name="stocktakezone", constraint=models.UniqueConstraint(fields=("session", "name"), name="uniq_stocktake_zone_name")),
        migrations.AddConstraint(model_name="stocktakecount", constraint=models.UniqueConstraint(fields=("session", "zone", "product"), name="uniq_product_per_stocktake_zone")),
    ]
