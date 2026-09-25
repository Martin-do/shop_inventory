from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Product, StockMovement, StockReceipt, StockReceiptLine, UserProfile
from .stocktake_models import StocktakeCount, StocktakeSession, StocktakeZone


class StockReceiptWorkflowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner_receipts", "owner@example.com", "pw")
        self.clerk = User.objects.create_user("receipt_clerk", password="pw")
        UserProfile.objects.create(user=self.clerk, role=UserProfile.ROLE_STOCK_CLERK)
        self.product = Product.objects.create(
            name="Paracetamol Syrup",
            barcode="RCPT-001",
            selling_price=Decimal("1200.00"),
        )

    def _draft_receipt(self):
        self.client.force_login(self.clerk)
        response = self.client.post(reverse("stock_receipt_create"), {
            "reference": "Supplier delivery A",
            "supplier": "Health Supplier",
            "invoice_reference": "INV-100",
        })
        receipt = StockReceipt.objects.get()
        self.assertRedirects(response, reverse("stock_receipt_detail", args=[receipt.pk]))
        return receipt

    def test_entry_and_submission_do_not_change_live_stock(self):
        receipt = self._draft_receipt()

        response = self.client.post(reverse("stock_receipt_add_line", args=[receipt.pk]), {
            "barcode": self.product.barcode,
            "quantity": "7",
            "note": "One carton opened",
        })
        self.assertRedirects(response, reverse("stock_receipt_detail", args=[receipt.pk]))
        line = receipt.lines.get(product=self.product)
        self.assertEqual(line.quantity_received, 7)
        self.assertEqual(self.product.stock_on_hand, 0)

        self.client.post(reverse("stock_receipt_submit", args=[receipt.pk]))
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, StockReceipt.STATUS_SUBMITTED)
        self.assertEqual(self.product.stock_on_hand, 0)

    def test_approved_receipt_adds_delta_to_existing_stock(self):
        StockMovement.objects.create(
            product=self.product,
            movement_type=StockMovement.RECEIVE,
            quantity=13,
            note="Existing balance",
        )
        receipt = self._draft_receipt()
        self.client.post(reverse("stock_receipt_add_line", args=[receipt.pk]), {
            "barcode": self.product.barcode,
            "quantity": "24",
        })
        self.client.post(reverse("stock_receipt_submit", args=[receipt.pk]))

        self.client.force_login(self.owner)
        line = receipt.lines.get()
        self.client.post(reverse("stock_receipt_review_line", args=[receipt.pk, line.pk]), {
            "approved_quantity": "24",
        })
        response = self.client.post(reverse("stock_receipt_apply", args=[receipt.pk]))

        self.assertRedirects(response, reverse("stock_receipt_detail", args=[receipt.pk]))
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, StockReceipt.STATUS_APPLIED)
        self.assertEqual(self.product.stock_on_hand, 37)
        movement = self.product.movements.filter(movement_type=StockMovement.RECEIVE).order_by("-id").first()
        self.assertEqual(movement.quantity, 24)
        self.assertEqual(movement.actor, self.owner)

    def test_reviewer_can_correct_quantity_before_apply(self):
        receipt = self._draft_receipt()
        self.client.post(reverse("stock_receipt_add_line", args=[receipt.pk]), {
            "barcode": self.product.barcode,
            "quantity": "10",
        })
        self.client.post(reverse("stock_receipt_submit", args=[receipt.pk]))

        self.client.force_login(self.owner)
        line = receipt.lines.get()
        self.client.post(reverse("stock_receipt_review_line", args=[receipt.pk, line.pk]), {
            "approved_quantity": "8",
        })
        self.client.post(reverse("stock_receipt_apply", args=[receipt.pk]))

        line.refresh_from_db()
        self.assertEqual(line.approved_quantity, 8)
        self.assertEqual(self.product.stock_on_hand, 8)

    def test_apply_is_blocked_while_product_is_in_active_stocktake(self):
        receipt = self._draft_receipt()
        self.client.post(reverse("stock_receipt_add_line", args=[receipt.pk]), {
            "barcode": self.product.barcode,
            "quantity": "5",
        })
        self.client.post(reverse("stock_receipt_submit", args=[receipt.pk]))

        session = StocktakeSession.objects.create(
            name="Still counting",
            created_by=self.owner,
            status=StocktakeSession.STATUS_COUNTING,
        )
        zone = StocktakeZone.objects.create(session=session, name="Main")
        StocktakeCount.objects.create(
            session=session,
            zone=zone,
            product=self.product,
            good_quantity=3,
            approved_good_quantity=3,
            counted_by=self.owner,
        )

        self.client.force_login(self.owner)
        line = receipt.lines.get()
        self.client.post(reverse("stock_receipt_review_line", args=[receipt.pk, line.pk]), {
            "approved_quantity": "5",
        })
        self.client.post(reverse("stock_receipt_apply", args=[receipt.pk]))

        receipt.refresh_from_db()
        self.assertEqual(receipt.status, StockReceipt.STATUS_SUBMITTED)
        self.assertEqual(self.product.stock_on_hand, 0)

    def test_new_product_can_be_registered_without_immediate_stock(self):
        receipt = self._draft_receipt()
        self.client.post(reverse("stock_receipt_add_line", args=[receipt.pk]), {
            "barcode": "NEW-RCPT-001",
            "name": "New Medicine",
            "quantity": "6",
            "selling_price": "2500.00",
            "cost_price": "1800.00",
            "reorder_level": "3",
        })

        product = Product.objects.get(barcode="NEW-RCPT-001")
        self.assertEqual(product.stock_on_hand, 0)
        self.assertEqual(receipt.lines.get(product=product).quantity_received, 6)


class SafeProductDeleteTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner_delete", "delete@example.com", "pw")
        self.client.force_login(self.owner)

    def test_unused_product_can_be_deleted(self):
        product = Product.objects.create(
            name="Mistake Product",
            barcode="DELETE-ME",
            selling_price=Decimal("100.00"),
        )
        response = self.client.post(reverse("product_delete", args=[product.pk]))
        self.assertRedirects(response, reverse("product_list"))
        self.assertFalse(Product.objects.filter(pk=product.pk).exists())

    def test_product_with_stock_history_cannot_be_deleted(self):
        product = Product.objects.create(
            name="Historical Product",
            barcode="KEEP-ME",
            selling_price=Decimal("100.00"),
        )
        StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.RECEIVE,
            quantity=1,
            note="History",
        )
        response = self.client.post(reverse("product_delete", args=[product.pk]))
        self.assertRedirects(response, reverse("product_list"))
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())
