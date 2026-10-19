import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Product, Sale, StockMovement, StoreSettings, UserProfile


class HardenedOfflineSyncTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cashier", password="test-pass")
        UserProfile.objects.create(user=self.cashier, role=UserProfile.ROLE_CASHIER)
        self.stock_clerk = User.objects.create_user(username="stock", password="test-pass")
        UserProfile.objects.create(user=self.stock_clerk, role=UserProfile.ROLE_STOCK_CLERK)
        self.product = Product.objects.create(
            name="Test Item",
            barcode="123456789",
            selling_price=Decimal("250.00"),
            cost_price=Decimal("150.00"),
        )
        StockMovement.objects.create(
            product=self.product,
            movement_type=StockMovement.RECEIVE,
            quantity=5,
            note="Opening test stock",
        )
        StoreSettings.get_solo()
        self.url = reverse("api_sync_offline")

    def payload(self, **overrides):
        data = {
            "temp_receipt": "OFFLINE-TEST-001",
            "cashier_name": "Forged Cashier",
            "discount_amount": "0.00",
            "tax_rate": "99.00",
            "amount_paid": "1000.00",
            "items": [
                {
                    "barcode": self.product.barcode,
                    "quantity": 2,
                    "unit_price": "1.00",
                }
            ],
        }
        data.update(overrides)
        return {"sales": [data]}

    def post(self, payload):
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_cashier_can_sync_and_server_price_is_authoritative(self):
        self.client.force_login(self.cashier)
        response = self.post(self.payload())
        self.assertEqual(response.status_code, 200)
        sale = Sale.objects.get()
        self.assertEqual(sale.total, Decimal("500.00"))
        self.assertEqual(sale.items.get().unit_price, Decimal("250.00"))
        self.assertEqual(sale.cashier_name, "cashier")
        self.assertEqual(self.product.stock_on_hand, 3)

    def test_repeated_client_reference_is_idempotent(self):
        self.client.force_login(self.cashier)
        first = self.post(self.payload())
        second = self.post(self.payload())
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertTrue(second.json()["synced"][0]["duplicate"])
        self.assertEqual(self.product.stock_on_hand, 3)

    def test_insufficient_stock_is_rejected_without_partial_sale(self):
        self.client.force_login(self.cashier)
        response = self.post(
            self.payload(
                temp_receipt="OFFLINE-TEST-002",
                items=[{"barcode": self.product.barcode, "quantity": 6}],
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(self.product.stock_on_hand, 5)
        self.assertIn("Insufficient stock", response.json()["errors"][0]["error"])

    def test_negative_quantity_is_rejected(self):
        self.client.force_login(self.cashier)
        response = self.post(
            self.payload(
                temp_receipt="OFFLINE-TEST-003",
                items=[{"barcode": self.product.barcode, "quantity": -2}],
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(self.product.stock_on_hand, 5)

    def test_stock_clerk_cannot_sync_sales(self):
        self.client.force_login(self.stock_clerk)
        response = self.post(self.payload())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Sale.objects.count(), 0)

    def test_discount_above_subtotal_is_rejected(self):
        self.client.force_login(self.cashier)
        response = self.post(
            self.payload(
                temp_receipt="OFFLINE-TEST-004",
                discount_amount="600.00",
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)


    def test_pack_sale_uses_pack_price_and_deducts_base_units(self):
        self.product.units_per_pack = 5
        self.product.pack_selling_price = Decimal("1100.00")
        self.product.save(update_fields=["units_per_pack", "pack_selling_price"])
        StockMovement.objects.create(
            product=self.product,
            movement_type=StockMovement.RECEIVE,
            quantity=15,
            note="Extra pack-sale stock",
        )
        self.client.force_login(self.cashier)

        response = self.post(self.payload(
            temp_receipt="OFFLINE-PACK-001",
            amount_paid="2200.00",
            items=[{
                "barcode": self.product.barcode,
                "sale_unit": "pack",
                "sale_quantity": 2,
                "unit_price": "1.00",
            }],
        ))

        self.assertEqual(response.status_code, 200)
        sale = Sale.objects.get()
        item = sale.items.get()
        self.assertEqual(sale.total, Decimal("2200.00"))
        self.assertEqual(item.sale_unit, "pack")
        self.assertEqual(item.sale_quantity, 2)
        self.assertEqual(item.quantity, 10)
        self.assertEqual(item.units_per_pack_snapshot, 5)
        self.assertEqual(item.unit_price, Decimal("1100.00"))
        self.assertEqual(self.product.stock_on_hand, 10)

    def test_pack_sale_uses_unit_price_times_pack_size_when_pack_price_blank(self):
        self.product.units_per_pack = 4
        self.product.pack_selling_price = None
        self.product.save(update_fields=["units_per_pack", "pack_selling_price"])
        StockMovement.objects.create(
            product=self.product,
            movement_type=StockMovement.RECEIVE,
            quantity=7,
            note="Extra pack-sale stock",
        )
        self.client.force_login(self.cashier)

        response = self.post(self.payload(
            temp_receipt="OFFLINE-PACK-002",
            amount_paid="1000.00",
            items=[{
                "barcode": self.product.barcode,
                "sale_unit": "pack",
                "sale_quantity": 1,
            }],
        ))

        self.assertEqual(response.status_code, 200)
        item = Sale.objects.get().items.get()
        self.assertEqual(item.unit_price, Decimal("1000.00"))
        self.assertEqual(item.quantity, 4)

    def test_pack_sale_is_rejected_when_product_has_no_pack_configuration(self):
        self.client.force_login(self.cashier)
        response = self.post(self.payload(
            temp_receipt="OFFLINE-PACK-003",
            items=[{
                "barcode": self.product.barcode,
                "sale_unit": "pack",
                "sale_quantity": 1,
            }],
        ))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertIn("not configured for pack sales", response.json()["errors"][0]["error"])

    def test_mixed_pack_and_unit_sale_checks_combined_base_stock(self):
        self.product.units_per_pack = 3
        self.product.save(update_fields=["units_per_pack"])
        self.client.force_login(self.cashier)

        response = self.post(self.payload(
            temp_receipt="OFFLINE-PACK-004",
            items=[
                {"barcode": self.product.barcode, "sale_unit": "pack", "sale_quantity": 1},
                {"barcode": self.product.barcode, "sale_unit": "unit", "sale_quantity": 3},
            ],
        ))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertIn("Insufficient stock", response.json()["errors"][0]["error"])
