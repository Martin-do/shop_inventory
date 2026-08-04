from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import AuditLog, Product, Sale, StockMovement, UserProfile


class AuditHistoryTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="pass", is_staff=True)
        UserProfile.objects.create(user=self.admin, role=UserProfile.ROLE_ADMIN)
        self.cashier = User.objects.create_user(username="cashier", password="pass")
        UserProfile.objects.create(user=self.cashier, role=UserProfile.ROLE_CASHIER)

    def test_product_create_and_update_capture_before_after(self):
        self.client.force_login(self.admin)
        product = Product.objects.create(
            name="Milk", barcode="111", selling_price=Decimal("1000.00")
        )
        product.selling_price = Decimal("1200.00")
        product.save()

        create_log = AuditLog.objects.filter(object_type="inventory.product", object_id=str(product.pk), action="create").first()
        update_log = AuditLog.objects.filter(object_type="inventory.product", object_id=str(product.pk), action="update").first()
        self.assertIsNotNone(create_log)
        self.assertEqual(create_log.actor, self.admin)
        self.assertIsNotNone(update_log)
        self.assertEqual(update_log.before["selling_price"], "1000.00")
        self.assertEqual(update_log.after["selling_price"], "1200.00")

    def test_sale_and_stock_movement_receive_authenticated_actor(self):
        self.client.force_login(self.cashier)
        product = Product.objects.create(name="Bread", barcode="222", selling_price=Decimal("500.00"))
        sale = Sale.objects.create(total=Decimal("500.00"), amount_paid=Decimal("500.00"))
        movement = StockMovement.objects.create(
            product=product, movement_type=StockMovement.SALE, quantity=-1, note="Test"
        )
        sale.refresh_from_db()
        movement.refresh_from_db()
        self.assertEqual(sale.cashier, self.cashier)
        self.assertEqual(movement.actor, self.cashier)

    def test_receipt_number_uses_primary_key_and_is_unique(self):
        first = Sale.objects.create(total=1, amount_paid=1)
        second = Sale.objects.create(total=1, amount_paid=1)
        self.assertNotEqual(first.receipt_number, second.receipt_number)
        self.assertTrue(first.receipt_number.endswith(f"{first.pk:06d}"))

    def test_client_reference_is_unique(self):
        Sale.objects.create(total=1, amount_paid=1, client_reference="OFFLINE-1")
        with self.assertRaises(Exception):
            Sale.objects.create(total=1, amount_paid=1, client_reference="OFFLINE-1")

    def test_only_admin_can_view_history(self):
        self.client.force_login(self.cashier)
        response = self.client.get(reverse("audit_history"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.admin)
        response = self.client.get(reverse("audit_history"))
        self.assertEqual(response.status_code, 200)

    def test_history_export_available_to_admin(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("audit_export_csv"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("staff_action_history.csv", response["Content-Disposition"])
