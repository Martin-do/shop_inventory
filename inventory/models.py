from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class ProductQuerySet(models.QuerySet):
    def with_stock(self):
        """Annotate each product with ``stock`` to avoid per-row aggregate queries."""
        return self.annotate(stock=Coalesce(Sum("movements__quantity"), 0))


class Product(models.Model):
    name = models.CharField(max_length=180)
    barcode = models.CharField(max_length=80, unique=True, db_index=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, blank=True, null=True)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    reorder_level = models.PositiveIntegerField(default=5)
    is_active = models.BooleanField(default=True)
    image = models.ImageField(upload_to="products/", blank=True, null=True)
    variant = models.CharField(max_length=80, blank=True, help_text="e.g. 1L, 1.5L, Pack of 6")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.barcode})"

    @property
    def stock_on_hand(self):
        if "stock" in self.__dict__:
            return self.__dict__["stock"]
        total = self.movements.aggregate(total=Sum("quantity"))["total"]
        return total or 0

    @property
    def is_low_stock(self):
        return self.stock_on_hand <= self.reorder_level


class StockMovement(models.Model):
    RECEIVE = "receive"
    SALE = "sale"
    ADJUSTMENT = "adjustment"
    RETURN = "return"
    MOVEMENT_TYPES = [
        (RECEIVE, "Received stock"),
        (SALE, "Sale"),
        (ADJUSTMENT, "Adjustment"),
        (RETURN, "Return"),
    ]

    product = models.ForeignKey(Product, related_name="movements", on_delete=models.PROTECT)
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity = models.IntegerField()
    note = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.product} {self.quantity:+d}"


class Customer(models.Model):
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        if self.phone:
            return f"{self.name} ({self.phone})"
        return self.name


class StoreSettings(models.Model):
    store_name = models.CharField(max_length=150, default="My Shop")
    default_tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"), help_text="Default tax percentage")
    enable_tax = models.BooleanField(default=False)
    receipt_header = models.TextField(blank=True, help_text="Text shown at top of receipts")
    receipt_footer = models.TextField(blank=True, help_text="Text shown at bottom of receipts")
    store_logo = models.ImageField(upload_to="store/", blank=True, null=True, help_text="Store logo shown on receipts")
    google_drive_folder_id = models.CharField(max_length=128, blank=True, null=True)
    google_service_account_json = models.TextField(blank=True, null=True, help_text="Paste Google Service Account credentials.json contents here")
    auto_backup_enabled = models.BooleanField(default=False)
    auto_backup_interval_hours = models.IntegerField(default=24)

    class Meta:
        verbose_name = "Store Settings"
        verbose_name_plural = "Store Settings"

    def __str__(self):
        return "Store Settings"

    @classmethod
    def get_solo(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj


class Sale(models.Model):
    created_at = models.DateTimeField(default=timezone.now)
    cashier_name = models.CharField(max_length=120, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, blank=True, null=True, related_name="sales")
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    
    STATUS_COMPLETED = "completed"
    STATUS_REVERTED = "reverted"
    STATUS_CHOICES = [
        (STATUS_COMPLETED, "Completed"),
        (STATUS_REVERTED, "Reverted"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_COMPLETED)
    receipt_number = models.CharField(max_length=50, unique=True, blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Sale {self.receipt_number or self.pk} - {self.total}"

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            date_str = self.created_at.strftime("%Y%m%d") if self.created_at else timezone.now().strftime("%Y%m%d")
            sales_today = self.__class__.objects.filter(receipt_number__startswith=f"INV-{date_str}-")
            count = sales_today.count()
            while True:
                number = f"INV-{date_str}-{(count + 1):04d}"
                if not self.__class__.objects.filter(receipt_number=number).exists():
                    self.receipt_number = number
                    break
                count += 1
        super().save(*args, **kwargs)

    @property
    def change_due(self):
        return self.amount_paid - self.total


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.product} x {self.quantity}"


class BackupLog(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=[('success', 'Success'), ('failed', 'Failed')])
    file_name = models.CharField(max_length=255)
    file_size_bytes = models.BigIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"Backup {self.file_name} - {self.status} at {self.timestamp}"
