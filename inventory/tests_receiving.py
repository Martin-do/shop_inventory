from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from .models import Product, StockMovement, StockReceipt, StockReceiptLine


def grant(user, *codenames):
    user.user_permissions.add(*Permission.objects.filter(
        content_type__app_label="inventory",
        codename__in=codenames,
    ))
    return User.objects.get(pk=user.pk)


class StockReceiptWorkflowTests(TestCase):
    def setUp(self):
        self.receiver = User.objects.create_user("receiver", password="pw")
        grant(self.receiver, "receive_stock")
        self.approver = User.objects.create_user("approver", password="pw")
        grant(self.approver, "approve_stock_receipts")
        self.product = Product.objects.create(
            name="Paracetamol",
            barcode="RX-001",
            selling_price=Decimal("100.00"),
        )

    def _create_receipt(self):
        self.client.force_login(self.receiver)
        response = self.client.post(reverse("stock_receipt_create"), {
            "supplier": "Test Supplier",
            "reference": "INV-001",
            "notes": "Morning delivery",
        })
        receipt = StockReceipt.objects.get(reference="INV-001")
        self.assertRedirects(response, reverse("stock_receipt_detail", args=[receipt.pk]))
        return receipt

    def test_receipt_stays_pending_until_authorised_apply(self):
        receipt = self._create_receipt()

        response = self.client.post(reverse("stock_receipt_add_line", args=[receipt.pk]), {
            "product_id": self.product.pk,
            "quantity": 5,
            "note": "Box A",
        })
        self.assertRedirects(response, reverse("stock_receipt_detail", args=[receipt.pk]))
        self.assertEqual(self.product.stock_on_hand, 0)
        self.assertEqual(StockMovement.objects.count(), 0)

        self.client.post(reverse("stock_receipt_submit", args=[receipt.pk]))
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, StockReceipt.STATUS_SUBMITTED)
        self.assertEqual(self.product.stock_on_hand, 0)

        self.client.force_login(self.approver)
        self.assertEqual(
            self.client.get(reverse("stock_receipt_detail", args=[receipt.pk])).status_code,
            200,
        )
        response = self.client.post(reverse("stock_receipt_apply", args=[receipt.pk]))
        self.assertRedirects(response, reverse("stock_receipt_detail", args=[receipt.pk]))

        receipt.refresh_from_db()
        self.assertEqual(receipt.status, StockReceipt.STATUS_APPLIED)
        self.assertEqual(self.product.stock_on_hand, 5)
        movement = StockMovement.objects.get(product=self.product, movement_type=StockMovement.RECEIVE)
        self.assertEqual(movement.quantity, 5)
        self.assertEqual(movement.actor, self.approver)
        self.assertIn("INV-001", movement.note)

    def test_approver_can_correct_submitted_line_before_apply(self):
        receipt = self._create_receipt()
        self.client.post(reverse("stock_receipt_add_line", args=[receipt.pk]), {
            "product_id": self.product.pk,
            "quantity": 4,
        })
        line = StockReceiptLine.objects.get(receipt=receipt, product=self.product)
        self.client.post(reverse("stock_receipt_submit", args=[receipt.pk]))

        self.client.force_login(self.approver)
        response = self.client.post(
            reverse("stock_receipt_update_line", args=[receipt.pk, line.pk]),
            {"quantity": 7, "note": "Verified against invoice"},
        )
        self.assertRedirects(response, reverse("stock_receipt_detail", args=[receipt.pk]))
        line.refresh_from_db()
        self.assertEqual(line.quantity, 7)

        self.client.post(reverse("stock_receipt_apply", args=[receipt.pk]))
        self.assertEqual(self.product.stock_on_hand, 7)

    def test_duplicate_product_line_is_not_silently_double_counted(self):
        receipt = self._create_receipt()
        url = reverse("stock_receipt_add_line", args=[receipt.pk])
        self.client.post(url, {"product_id": self.product.pk, "quantity": 3})
        self.client.post(url, {"product_id": self.product.pk, "quantity": 8})

        self.assertEqual(StockReceiptLine.objects.filter(receipt=receipt).count(), 1)
        self.assertEqual(StockReceiptLine.objects.get(receipt=receipt).quantity, 3)

    def test_barcode_sized_receipt_quantity_is_rejected(self):
        receipt = self._create_receipt()
        self.client.post(reverse("stock_receipt_add_line", args=[receipt.pk]), {
            "product_id": self.product.pk,
            "quantity": 6034000231113,
        })
        self.assertFalse(StockReceiptLine.objects.filter(receipt=receipt).exists())
        self.assertEqual(self.product.stock_on_hand, 0)

    def test_unknown_product_needs_create_permission_and_remains_pending(self):
        receipt = self._create_receipt()
        url = reverse("stock_receipt_add_line", args=[receipt.pk])
        payload = {
            "barcode": "NEW-7300",
            "name": "New Product",
            "selling_price": "30.00",
            "cost_price": "20.00",
            "reorder_level": 4,
            "quantity": 6,
        }

        self.client.post(url, payload)
        self.assertFalse(Product.objects.filter(barcode="NEW-7300").exists())

        grant(self.receiver, "create_products")
        self.client.force_login(self.receiver)
        self.client.post(url, payload)

        product = Product.objects.get(barcode="NEW-7300")
        self.assertEqual(product.cost_price, Decimal("0.00"))
        self.assertEqual(product.stock_on_hand, 0)
        self.assertEqual(
            StockReceiptLine.objects.get(receipt=receipt, product=product).quantity,
            6,
        )

    def test_receiver_cannot_apply_submitted_receipt(self):
        receipt = self._create_receipt()
        self.client.post(reverse("stock_receipt_add_line", args=[receipt.pk]), {
            "product_id": self.product.pk,
            "quantity": 2,
        })
        self.client.post(reverse("stock_receipt_submit", args=[receipt.pk]))
        response = self.client.post(reverse("stock_receipt_apply", args=[receipt.pk]))
        self.assertEqual(response.status_code, 302)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, StockReceipt.STATUS_SUBMITTED)
        self.assertEqual(self.product.stock_on_hand, 0)


class SafeProductDeleteTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("manager-delete", password="pw")
        grant(self.manager, "view_products", "delete_products", "toggle_products")
        self.client.force_login(self.manager)

    def test_unused_inactive_product_can_be_permanently_deleted(self):
        product = Product.objects.create(
            name="Mistake",
            barcode="DELETE-001",
            selling_price=Decimal("1.00"),
            is_active=False,
        )
        response = self.client.post(reverse("product_delete", args=[product.pk]))
        self.assertRedirects(response, reverse("product_list"))
        self.assertFalse(Product.objects.filter(pk=product.pk).exists())

    def test_active_product_must_be_deactivated_first(self):
        product = Product.objects.create(
            name="Still Active",
            barcode="DELETE-002",
            selling_price=Decimal("1.00"),
        )
        response = self.client.post(reverse("product_delete", args=[product.pk]))
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())

    def test_historical_product_cannot_be_deleted_even_when_inactive(self):
        product = Product.objects.create(
            name="Historical",
            barcode="DELETE-003",
            selling_price=Decimal("1.00"),
            is_active=False,
        )
        StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.RECEIVE,
            quantity=1,
            note="Historical stock",
        )
        response = self.client.post(reverse("product_delete", args=[product.pk]))
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())
