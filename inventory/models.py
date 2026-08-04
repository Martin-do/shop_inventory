from decimal import Decimal

from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone


class UserProfile(models.Model):
    ROLE_CASHIER = "cashier"
    ROLE_STOCK_CLERK = "stock_clerk"
    ROLE_ADMIN = "admin"
    ROLE_CHOICES = [
        (ROLE_CASHIER, "Cashier (POS Only)"),
        (ROLE_STOCK_CLERK, "Stock Clerk (Receive Stock & Products)"),
        (ROLE_ADMIN, "Admin/Manager (Full Access)"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_CASHIER)

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"


def get_user_role(user):
    if not user or user.is_anonymous:
        return ""
    if user.is_superuser:
        return UserProfile.ROLE_ADMIN
    try:
        return user.profile.role
    except (UserProfile.DoesNotExist, AttributeError):
        return UserProfile.ROLE_ADMIN if user.is_staff else UserProfile.ROLE_CASHIER


User.role = property(get_user_role)


class Category(models.Model):
    name = models.CharField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class ProductQuerySet(models.QuerySet):
    def with_stock(self):
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
    actor = models.ForeignKey(User, related_name="stock_movements", on_delete=models.PROTECT, null=True, blank=True)
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
        return f"{self.name} ({self.phone})" if self.phone else self.name


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
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Sale(models.Model):
    created_at = models.DateTimeField(default=timezone.now)
    cashier = models.ForeignKey(User, related_name="sales", on_delete=models.PROTECT, null=True, blank=True)
    cashier_name = models.CharField(max_length=120, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, blank=True, null=True, related_name="sales")
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    client_reference = models.CharField(max_length=120, unique=True, null=True, blank=True)

    STATUS_COMPLETED = "completed"
    STATUS_REVERTED = "reverted"
    STATUS_CHOICES = [(STATUS_COMPLETED, "Completed"), (STATUS_REVERTED, "Reverted")]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_COMPLETED)
    receipt_number = models.CharField(max_length=50, unique=True, blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Sale {self.receipt_number or self.pk} - {self.total}"

    def save(self, *args, **kwargs):
        creating = self.pk is None
        if creating and not self.cashier_name and self.cashier:
            self.cashier_name = self.cashier.get_full_name() or self.cashier.username
        super().save(*args, **kwargs)
        if not self.receipt_number:
            date_str = self.created_at.strftime("%Y%m%d")
            receipt = f"INV-{date_str}-{self.pk:06d}"
            type(self).objects.filter(pk=self.pk, receipt_number__isnull=True).update(receipt_number=receipt)
            self.receipt_number = receipt

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


class AuditLog(models.Model):
    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_LOGIN = "login"
    ACTION_LOGOUT = "logout"
    ACTION_VIEW = "view"
    ACTION_EXPORT = "export"
    ACTION_SYNC = "sync"
    ACTION_REVERT = "revert"
    ACTION_CHOICES = [
        (ACTION_CREATE, "Created"), (ACTION_UPDATE, "Updated"),
        (ACTION_DELETE, "Deleted"), (ACTION_LOGIN, "Logged in"),
        (ACTION_LOGOUT, "Logged out"), (ACTION_VIEW, "Viewed"),
        (ACTION_EXPORT, "Exported"), (ACTION_SYNC, "Synced"),
        (ACTION_REVERT, "Reverted"),
    ]

    actor = models.ForeignKey(User, related_name="audit_logs", on_delete=models.SET_NULL, null=True, blank=True)
    actor_name = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    object_type = models.CharField(max_length=100, blank=True, db_index=True)
    object_id = models.CharField(max_length=100, blank=True, db_index=True)
    object_label = models.CharField(max_length=240, blank=True)
    summary = models.CharField(max_length=240)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    request_method = models.CharField(max_length=10, blank=True)
    request_path = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["object_type", "object_id", "-created_at"])]

    def __str__(self):
        return f"{self.actor_name or 'System'} {self.action}: {self.summary}"


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
