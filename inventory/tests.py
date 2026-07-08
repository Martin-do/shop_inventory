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
        User.objects.create_user("cashier", password="pw")
        self.client.force_login(User.objects.get(username="cashier"))
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
        self.user = User.objects.create_user("cashier", password="pw")
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
        self.assertRedirects(response, reverse("sale_detail", args=[sale.pk]))
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
