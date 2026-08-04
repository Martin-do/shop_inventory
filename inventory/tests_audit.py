from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Product, Sale, SaleItem, StockMovement, UserProfile


class AuditWorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="manager", password="pass12345")
        UserProfile.objects.create(user=self.admin, role=UserProfile.ROLE_ADMIN)
        self.cashier = User.objects.create_user(username="cashier", password="pass12345")
        UserProfile.objects.create(user=self.cashier, role=UserProfile.ROLE_CASHIER)
        self.product = Product.objects.create(
            name="Test Product", barcode="AUDIT-001", selling_price="100.00"
        )
        StockMovement.objects.create(
            product=self.product,
            movement_type=StockMovement.RECEIVE,
            quantity=10,
            note="Opening stock",
        )
        self.sale = Sale.objects.create(
            cashier_name="cashier", total="200.00", amount_paid="200.00"
        )
        SaleItem.objects.create(
            sale=self.sale,
            product=self.product,
            quantity=2,
            unit_price="100.00",
            line_total="200.00",
        )
        StockMovement.objects.create(
            product=self.product,
            movement_type=StockMovement.SALE,
            quantity=-2,
            note=f"Sale #{self.sale.pk}",
        )

    def test_cashier_cannot_revert_sale(self):
        self.client.force_login(self.cashier)
        response = self.client.post(
            reverse("sale_revert", args=[self.sale.pk]),
            {"reversal_reason": "Customer returned goods"},
        )
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.status, Sale.STATUS_COMPLETED)
        self.assertEqual(response.status_code, 302)

    def test_admin_reversal_requires_reason(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("sale_revert", args=[self.sale.pk]), {"reversal_reason": "no"})
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.status, Sale.STATUS_COMPLETED)

    def test_admin_reversal_records_actor_and_reason(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("sale_revert", args=[self.sale.pk]),
            {"reversal_reason": "Customer returned damaged item"},
        )
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.status, Sale.STATUS_REVERTED)
        movement = StockMovement.objects.filter(
            product=self.product,
            movement_type=StockMovement.RETURN,
        ).latest("id")
        self.assertIn("manager", movement.note)
        self.assertIn("Customer returned damaged item", movement.note)

    def test_sales_csv_marks_reverted_rows_as_not_net_sales(self):
        self.sale.status = Sale.STATUS_REVERTED
        self.sale.save(update_fields=["status"])
        self.client.force_login(self.admin)
        response = self.client.get(reverse("export_sales_csv"))
        content = response.content.decode("utf-8")
        self.assertIn("Counts Toward Net Sales", content)
        self.assertIn("reverted,NO", content)
