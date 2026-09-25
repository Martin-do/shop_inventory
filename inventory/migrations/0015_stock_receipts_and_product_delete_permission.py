from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


def add_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    ContentType = apps.get_model("contenttypes", "ContentType")

    content_type, _ = ContentType.objects.get_or_create(
        app_label="inventory",
        model="userprofile",
    )

    permissions = {}
    for codename, name in [
        ("approve_stock_receipts", "Can approve and apply stock receipts"),
        ("delete_products", "Can permanently delete unused products"),
    ]:
        permission, _ = Permission.objects.get_or_create(
            content_type=content_type,
            codename=codename,
            defaults={"name": name},
        )
        if permission.name != name:
            permission.name = name
            permission.save(update_fields=["name"])
        permissions[codename] = permission

    for group_name in ["Inventory supervisor", "Manager", "Owner / system administrator"]:
        group = Group.objects.filter(name=group_name).first()
        if group:
            group.permissions.add(permissions["approve_stock_receipts"])

    for group_name in ["Manager", "Owner / system administrator"]:
        group = Group.objects.filter(name=group_name).first()
        if group:
            group.permissions.add(permissions["delete_products"])


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0014_repair_staff_access"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StockReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("supplier", models.CharField(blank=True, max_length=180)),
                ("reference", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("status", models.CharField(
                    choices=[
                        ("draft", "Draft"),
                        ("submitted", "Awaiting approval"),
                        ("applied", "Applied"),
                        ("cancelled", "Cancelled"),
                    ],
                    db_index=True,
                    default="draft",
                    max_length=20,
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("applied_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="applied_stock_receipts",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("cancelled_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="cancelled_stock_receipts",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("created_by", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="created_stock_receipts",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("submitted_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="submitted_stock_receipts",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="StockReceiptLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ("note", models.CharField(blank=True, max_length=240)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("entered_by", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="stock_receipt_lines",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("product", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="receipt_lines",
                    to="inventory.product",
                )),
                ("receipt", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="lines",
                    to="inventory.stockreceipt",
                )),
            ],
            options={"ordering": ["created_at", "product__name"]},
        ),
        migrations.AddConstraint(
            model_name="stockreceiptline",
            constraint=models.UniqueConstraint(
                fields=("receipt", "product"),
                name="uniq_product_per_stock_receipt",
            ),
        ),
        migrations.RunPython(add_permissions, migrations.RunPython.noop),
    ]
