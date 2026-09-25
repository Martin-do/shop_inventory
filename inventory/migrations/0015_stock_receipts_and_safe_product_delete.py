from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


def create_new_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    ContentType = apps.get_model("contenttypes", "ContentType")

    content_type, _ = ContentType.objects.get_or_create(
        app_label="inventory",
        model="userprofile",
    )

    definitions = {
        "delete_products": "Can permanently delete unused products",
        "review_stock_receipts": "Can review submitted stock receipts",
        "apply_stock_receipts": "Can apply approved stock receipts",
    }
    perms = {}
    for codename, name in definitions.items():
        perm, _ = Permission.objects.get_or_create(
            content_type=content_type,
            codename=codename,
            defaults={"name": name},
        )
        if perm.name != name:
            perm.name = name
            perm.save(update_fields=["name"])
        perms[codename] = perm

    group_codes = {
        "Inventory supervisor": {"review_stock_receipts", "apply_stock_receipts"},
        "Manager": {"review_stock_receipts", "apply_stock_receipts", "delete_products"},
        "Owner / system administrator": {"review_stock_receipts", "apply_stock_receipts", "delete_products"},
    }
    for group_name, codes in group_codes.items():
        group = Group.objects.filter(name=group_name).first()
        if group:
            group.permissions.add(*(perms[code] for code in codes))


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0014_repair_staff_access"),
    ]

    operations = [
        migrations.CreateModel(
            name="StockReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference", models.CharField(max_length=180)),
                ("supplier", models.CharField(blank=True, max_length=180)),
                ("invoice_reference", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("submitted", "Awaiting approval"), ("applied", "Applied"), ("cancelled", "Cancelled")], db_index=True, default="draft", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("applied_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="applied_stock_receipts", to="auth.user")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_stock_receipts", to="auth.user")),
                ("submitted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="submitted_stock_receipts", to="auth.user")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="StockReceiptLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity_received", models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ("approved_quantity", models.PositiveIntegerField(blank=True, null=True)),
                ("note", models.CharField(blank=True, max_length=240)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved")], default="pending", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="approved_stock_receipt_lines", to="auth.user")),
                ("entered_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="entered_stock_receipt_lines", to="auth.user")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="receipt_lines", to="inventory.product")),
                ("receipt", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="inventory.stockreceipt")),
            ],
            options={"ordering": ["product__name"]},
        ),
        migrations.AddConstraint(
            model_name="stockreceiptline",
            constraint=models.UniqueConstraint(fields=("receipt", "product"), name="uniq_product_per_stock_receipt"),
        ),
        migrations.RunPython(create_new_permissions, migrations.RunPython.noop),
    ]
