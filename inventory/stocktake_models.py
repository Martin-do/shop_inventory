from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class StocktakeSession(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_COUNTING = "counting"
    STATUS_REVIEW = "review"
    STATUS_APPLIED = "applied"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_COUNTING, "Counting"),
        (STATUS_REVIEW, "Under review"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    name = models.CharField(max_length=180)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    blind_count = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_stocktakes")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="applied_stocktakes", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def can_count(self):
        return self.status in {self.STATUS_DRAFT, self.STATUS_COUNTING}

    @property
    def total_products(self):
        return self.counts.values("product_id").distinct().count()

    @property
    def total_good_units(self):
        return self.counts.aggregate(total=Sum("approved_good_quantity"))["total"] or 0


class StocktakeZone(models.Model):
    session = models.ForeignKey(StocktakeSession, on_delete=models.CASCADE, related_name="zones")
    name = models.CharField(max_length=150)
    description = models.CharField(max_length=240, blank=True)
    assigned_users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="stocktake_zones")
    is_complete = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="completed_stocktake_zones")

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["session", "name"], name="uniq_stocktake_zone_name")]

    def __str__(self):
        return f"{self.session.name} — {self.name}"


class StocktakeCount(models.Model):
    STATUS_COUNTED = "counted"
    STATUS_RECOUNT = "recount"
    STATUS_APPROVED = "approved"
    STATUS_CHOICES = [
        (STATUS_COUNTED, "Counted"),
        (STATUS_RECOUNT, "Recount required"),
        (STATUS_APPROVED, "Approved"),
    ]

    session = models.ForeignKey(StocktakeSession, on_delete=models.CASCADE, related_name="counts")
    zone = models.ForeignKey(StocktakeZone, on_delete=models.CASCADE, related_name="counts")
    product = models.ForeignKey("inventory.Product", on_delete=models.PROTECT, related_name="stocktake_counts")
    good_quantity = models.PositiveIntegerField(default=0)
    damaged_quantity = models.PositiveIntegerField(default=0)
    expired_quantity = models.PositiveIntegerField(default=0)
    reserved_quantity = models.PositiveIntegerField(default=0)
    approved_good_quantity = models.PositiveIntegerField(default=0)
    first_count_quantity = models.PositiveIntegerField(default=0)
    recount_quantity = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_COUNTED)
    note = models.CharField(max_length=240, blank=True)
    counted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stocktake_counts")
    counted_at = models.DateTimeField(auto_now=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_stocktake_counts")
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["zone__name", "product__name"]
        constraints = [models.UniqueConstraint(fields=["session", "zone", "product"], name="uniq_product_per_stocktake_zone")]

    def __str__(self):
        return f"{self.product} in {self.zone.name}: {self.good_quantity}"

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.first_count_quantity = self.good_quantity
        if self.status == self.STATUS_APPROVED and self.approved_good_quantity == 0:
            self.approved_good_quantity = self.recount_quantity if self.recount_quantity is not None else self.good_quantity
        super().save(*args, **kwargs)


class StocktakeEvent(models.Model):
    session = models.ForeignKey(StocktakeSession, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    event = models.CharField(max_length=80)
    message = models.CharField(max_length=240)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.message
