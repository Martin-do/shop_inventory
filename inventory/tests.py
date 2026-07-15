from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .forms import ProductForm
from .models import Category, Product, Sale, SaleItem, StockMovement


def make_product(barcode="1001", selling_price="10.00", reorder_level=5, stock=0):
    product = Product.objects.create(
        name=f"Item {barcode}",
        barcode=barcode,
        selling_price=Decimal(selling_price),
        reorder_level=reorder_level,
    )
    if stock:
        StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.RECEIVE,
            quantity=stock,
            note="Setup",
        )
    return product


class StockModelTests(TestCase):
    def test_stock_on_hand_sums_signed_movements(self):
        product = make_product(stock=10)
        StockMovement.objects.create(
            product=product, movement_type=StockMovement.SALE, quantity=-3
        )
        self.assertEqual(product.stock_on_hand, 7)

    def test_stock_on_hand_uses_annotation_without_extra_query(self):
        make_product(stock=4)
        annotated = Product.objects.with_stock().get()
        with self.assertNumQueries(0):
            self.assertEqual(annotated.stock_on_hand, 4)

    def test_is_low_stock(self):
        product = make_product(reorder_level=5, stock=5)
        self.assertTrue(product.is_low_stock)
        StockMovement.objects.create(
            product=product, movement_type=StockMovement.RECEIVE, quantity=1
        )
        self.assertFalse(Product.objects.with_stock().get().is_low_stock)


class AuthTests(TestCase):
    def test_protected_views_redirect_anonymous(self):
        for name in ["dashboard", "product_list", "pos", "reports"]:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("login"), response.url)

    def test_authenticated_access(self):
        user = User.objects.create_user("cashier", password="pw")
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("pos"))

        admin = User.objects.create_user("admin", password="pw", is_staff=True)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)


class ProductFormTests(TestCase):
    def test_duplicate_barcode_rejected(self):
        make_product(barcode="555")
        form = ProductForm(data={
            "name": "Another",
            "barcode": "555",
            "selling_price": "5.00",
            "reorder_level": 5,
            "opening_stock": 0,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("barcode", form.errors)

    def test_new_category_creates_and_assigns(self):
        form = ProductForm(data={
            "name": "Soda",
            "barcode": "8001",
            "selling_price": "3.00",
            "reorder_level": 5,
            "opening_stock": 0,
            "new_category": "Drinks",
        })
        self.assertTrue(form.is_valid(), form.errors)
        product = form.save()
        self.assertEqual(Category.objects.filter(name="Drinks").count(), 1)
        self.assertEqual(product.category.name, "Drinks")

    def test_new_category_reuses_existing_name(self):
        existing = Category.objects.create(name="Snacks")
        form = ProductForm(data={
            "name": "Chips",
            "barcode": "8002",
            "selling_price": "2.00",
            "reorder_level": 5,
            "opening_stock": 0,
            "new_category": "Snacks",
        })
        self.assertTrue(form.is_valid(), form.errors)
        product = form.save()
        self.assertEqual(Category.objects.filter(name="Snacks").count(), 1)
        self.assertEqual(product.category, existing)

    def test_invalid_submit_does_not_create_category(self):
        make_product(barcode="8003")
        form = ProductForm(data={
            "name": "Dup",
            "barcode": "8003",
            "selling_price": "2.00",
            "reorder_level": 5,
            "opening_stock": 0,
            "new_category": "Ghost",
        })
        self.assertFalse(form.is_valid())
        self.assertFalse(Category.objects.filter(name="Ghost").exists())

    def test_edit_keeps_same_barcode(self):
        product = make_product(barcode="777")
        form = ProductForm(
            data={
                "name": "Renamed",
                "barcode": "777",
                "selling_price": "9.00",
                "reorder_level": 3,
                "is_active": True,
            },
            instance=product,
        )
        self.assertTrue(form.is_valid(), form.errors)


class ProductViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cashier", password="pw", is_staff=True)
        self.client.force_login(self.user)

    def test_create_with_opening_stock_records_movement(self):
        response = self.client.post(reverse("product_create"), {
            "name": "Widget",
            "barcode": "9001",
            "selling_price": "12.50",
            "reorder_level": 5,
            "opening_stock": 8,
        })
        self.assertRedirects(response, reverse("product_list"))
        product = Product.objects.get(barcode="9001")
        self.assertEqual(product.stock_on_hand, 8)

    def test_update_changes_price(self):
        product = make_product(barcode="9002", selling_price="10.00")
        response = self.client.post(reverse("product_update", args=[product.pk]), {
            "name": product.name,
            "barcode": "9002",
            "selling_price": "15.00",
            "reorder_level": 5,
            "is_active": True,
        })
        self.assertRedirects(response, reverse("product_list"))
        product.refresh_from_db()
        self.assertEqual(product.selling_price, Decimal("15.00"))

    def test_toggle_active(self):
        product = make_product(barcode="9003")
        self.assertTrue(product.is_active)
        self.client.post(reverse("product_toggle_active", args=[product.pk]))
        product.refresh_from_db()
        self.assertFalse(product.is_active)


class CheckoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cashier", password="pw")
        self.client.force_login(self.user)
        self.product = make_product(barcode="4001", selling_price="10.00", stock=5)

    def _add_to_cart(self, quantity):
        return self.client.post(reverse("pos_add"), {"barcode": "4001", "quantity": quantity})

    def test_successful_checkout_decrements_stock(self):
        self._add_to_cart(2)
        response = self.client.post(reverse("pos_checkout"), {"amount_paid": "20.00", "cashier_name": "Sam"})
        sale = Sale.objects.get()
        self.assertRedirects(response, reverse("sale_receipt", args=[sale.pk]))
        self.assertEqual(sale.total, Decimal("20.00"))
        self.assertEqual(SaleItem.objects.count(), 1)
        self.assertEqual(self.product.stock_on_hand, 3)

    def test_add_to_cart_rejects_oversell(self):
        self._add_to_cart(99)
        self.assertEqual(self.client.session.get("cart", {}), {})

    def test_checkout_rejects_underpayment(self):
        self._add_to_cart(2)
        response = self.client.post(reverse("pos_checkout"), {"amount_paid": "5.00", "cashier_name": ""})
        self.assertRedirects(response, reverse("pos"))
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(self.product.stock_on_hand, 5)

    def test_checkout_rejects_when_stock_drops(self):
        self._add_to_cart(4)
        # Stock is removed after the item is already in the cart.
        StockMovement.objects.create(
            product=self.product, movement_type=StockMovement.ADJUSTMENT, quantity=-3
        )
        response = self.client.post(reverse("pos_checkout"), {"amount_paid": "40.00", "cashier_name": ""})
        self.assertRedirects(response, reverse("pos"))
        self.assertEqual(Sale.objects.count(), 0)

    def test_checkout_with_discount_and_tax(self):
        # Cart total: 2 items * 10.00 = 20.00
        # Discount: 5.00
        # Tax rate: 10%
        # Taxable amount: 20.00 - 5.00 = 15.00
        # Tax amount: 15.00 * 0.10 = 1.50
        # Final total: 15.00 + 1.50 = 16.50
        self._add_to_cart(2)
        response = self.client.post(reverse("pos_checkout"), {
            "amount_paid": "16.50",
            "cashier_name": "Sam",
            "discount_amount": "5.00",
            "tax_rate": "10.00",
        })
        sale = Sale.objects.get()
        self.assertRedirects(response, reverse("sale_receipt", args=[sale.pk]))
        self.assertEqual(sale.total, Decimal("16.50"))
        self.assertEqual(sale.discount_amount, Decimal("5.00"))
        self.assertEqual(sale.tax_amount, Decimal("1.50"))

    def test_checkout_with_customer(self):
        from .models import Customer
        customer = Customer.objects.create(name="Alice", phone="1234567890")
        self._add_to_cart(1)
        response = self.client.post(reverse("pos_checkout"), {
            "amount_paid": "10.00",
            "cashier_name": "Sam",
            "customer": customer.id,
        })
        sale = Sale.objects.get()
        self.assertEqual(sale.customer, customer)

    def test_revert_sale_restores_stock(self):
        self._add_to_cart(2)
        self.client.post(reverse("pos_checkout"), {"amount_paid": "20.00", "cashier_name": "Sam"})
        sale = Sale.objects.get()
        self.assertEqual(self.product.stock_on_hand, 3)
        self.assertEqual(sale.status, Sale.STATUS_COMPLETED)

        # Revert the sale
        response = self.client.post(reverse("sale_revert", args=[sale.pk]))
        self.assertRedirects(response, reverse("sale_receipt", args=[sale.pk]))
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_REVERTED)
        self.assertEqual(self.product.stock_on_hand, 5)

    def test_cannot_revert_already_reverted_sale(self):
        self._add_to_cart(1)
        self.client.post(reverse("pos_checkout"), {"amount_paid": "10.00", "cashier_name": "Sam"})
        sale = Sale.objects.get()
        self.client.post(reverse("sale_revert", args=[sale.pk]))
        
        # Try reverting again
        response = self.client.post(reverse("sale_revert", args=[sale.pk]))
        self.assertRedirects(response, reverse("sale_receipt", args=[sale.pk]))
        # Ensure it didn't add stock again
        self.assertEqual(self.product.stock_on_hand, 5)


from unittest.mock import patch
import os
from .models import BackupLog, StoreSettings
from .backup_utils import create_backup_zip, run_backup_job

class BackupTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user("admin", password="pw", is_staff=True)
        self.cashier_user = User.objects.create_user("cashier", password="pw", is_staff=False)
        self.product = make_product(barcode="4001", selling_price="10.00", stock=5)

    def test_create_backup_zip(self):
        zip_path, filename = create_backup_zip()
        self.assertTrue(os.path.exists(zip_path))
        self.assertTrue(filename.startswith("backup_"))
        self.assertTrue(filename.endswith(".zip"))
        if os.path.exists(zip_path):
            os.remove(zip_path)

    def test_backup_views_permissions(self):
        response = self.client.post(reverse("trigger_manual_backup"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.cashier_user)
        response = self.client.post(reverse("trigger_manual_backup"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.staff_user)
        response = self.client.post(reverse("trigger_manual_backup"))
        self.assertRedirects(response, reverse("settings_dashboard"))

    def test_run_backup_job_unconfigured(self):
        settings = StoreSettings.get_solo()
        settings.google_service_account_json = ""
        settings.google_drive_folder_id = ""
        settings.save()

        success, message = run_backup_job()
        self.assertFalse(success)
        self.assertIn("not configured", message)

        last_log = BackupLog.objects.first()
        self.assertIsNotNone(last_log)
        self.assertEqual(last_log.status, "failed")
        self.assertIn("not configured", last_log.error_message)

    @patch("inventory.backup_utils.upload_to_google_drive")
    def test_run_backup_job_configured_mocked(self, mock_upload):
        mock_upload.return_value = "mock_drive_file_id"

        settings = StoreSettings.get_solo()
        settings.google_service_account_json = '{"type": "service_account"}'
        settings.google_drive_folder_id = "mock_folder_id"
        settings.save()

        success, message = run_backup_job()
        self.assertTrue(success)
        self.assertIn("mock_drive_file_id", message)

        last_log = BackupLog.objects.first()
        self.assertEqual(last_log.status, "success")
        self.assertTrue(last_log.file_name.startswith("backup_"))
