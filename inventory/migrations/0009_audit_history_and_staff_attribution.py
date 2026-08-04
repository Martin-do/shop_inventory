import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0008_audit_hardening"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="cashier",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="sales", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="sale",
            name="client_reference",
            field=models.CharField(blank=True, max_length=120, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="actor",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="stock_movements", to=settings.AUTH_USER_MODEL),
        ),
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("actor_name", models.CharField(blank=True, max_length=150)),
                ("action", models.CharField(choices=[("create", "Created"), ("update", "Updated"), ("delete", "Deleted"), ("login", "Logged in"), ("logout", "Logged out"), ("view", "Viewed"), ("export", "Exported"), ("sync", "Synced"), ("revert", "Reverted")], max_length=20)),
                ("object_type", models.CharField(blank=True, db_index=True, max_length=100)),
                ("object_id", models.CharField(blank=True, db_index=True, max_length=100)),
                ("object_label", models.CharField(blank=True, max_length=240)),
                ("summary", models.CharField(max_length=240)),
                ("before", models.JSONField(blank=True, default=dict)),
                ("after", models.JSONField(blank=True, default=dict)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("request_method", models.CharField(blank=True, max_length=10)),
                ("request_path", models.CharField(blank=True, max_length=255)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_logs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["object_type", "object_id", "-created_at"], name="inventory_a_object__b3e659_idx"),
        ),
    ]
