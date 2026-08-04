from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
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
        create_response = self.client.post(
            reverse("product_create"),
            {
                "name": "Milk",
                "barcode": "111",
                "selling_price": "1000.00",
                "cost_price": "0.00",
                "reorder_level": 5,
                "opening_stock": 0,
                "is_active": True,
            },
        )
        self.assertEqual(create_response.status_code, 302)
        product = Product.objects.get(barcode="111")

        update_response = self.client.post(
            reverse("product_update", args=[product.pk]),
            {
                "name": "Milk",
                "barcode": "111",
                "selling_price": "1200.00",
                "cost_price": "0.00",
                "reorder_level": 5,
                "is_active": True,
                "stock_adjustment_reason": "Price-only update",
            },
        )
        self.assertEqual(update_response.status_code, 302)

        create_log = AuditLog.objects.filter(
            object_type="inventory.product", object_id=str(product.pk), action="create"
        ).first()
        update_log = AuditLog.objects.filter(
            object_type="inventory.product", object_id=str(product.pk), action="update"
        ).first()
        self.assertIsNotNone(create_log)
        self.assertEqual(create_log.actor, self.admin)
        self.assertIsNotNone(update_log)
        self.assertEqual(update_log.before["selling_price"], "1000.00")
        self.assertEqual(update_log.after["selling_price"], "1200.00")

    def test_sale_and_stock_movement_receive_authenticated_actor(self):
        self.client.force_login(self.cashier)
        product = Product.objects.create(
            name="Bread", barcode="222", selling_price=Decimal("500.00")
        )
        StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.RECEIVE,
            quantity=2,
            note="Opening stock",
        )

        self.client.post(reverse("pos_add"), {"barcode": "222", "quantity": 1})
        response = self.client.post(
            reverse("pos_checkout"),
            {"amount_paid": "500.00", "cashier_name": "Ignored client value"},
        )
        self.assertEqual(response.status_code, 302)

        sale = Sale.objects.get()
        movement = StockMovement.objects.filter(
            product=product, movement_type=StockMovement.SALE
        ).latest("id")
        self.assertEqual(sale.cashier, self.cashier)
        self.assertEqual(movement.actor, self.cashier)

    def test_receipt_number_uses_primary_key_and_is_unique(self):
        first = Sale.objects.create(total=1, amount_paid=1)
        second = Sale.objects.create(total=1, amount_paid=1)
        self.assertNotEqual(first.receipt_number, second.receipt_number)
        self.assertTrue(first.receipt_number.endswith(f"{first.pk:06d}"))

    def test_client_reference_is_unique(self):
        Sale.objects.create(total=1, amount_paid=1, client_reference="OFFLINE-1")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
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
