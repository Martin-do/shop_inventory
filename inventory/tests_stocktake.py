from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Category, Product, StockMovement, UserProfile
from .stocktake_models import StocktakeCount, StocktakeSession, StocktakeZone


class OpeningStocktakeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("manager", password="pw", is_staff=True)
        UserProfile.objects.create(user=self.admin, role=UserProfile.ROLE_ADMIN)
        self.clerk = User.objects.create_user("counter", password="pw", is_staff=True)
        UserProfile.objects.create(user=self.clerk, role=UserProfile.ROLE_STOCK_CLERK)
        self.other = User.objects.create_user("other", password="pw", is_staff=True)
        UserProfile.objects.create(user=self.other, role=UserProfile.ROLE_STOCK_CLERK)
        self.product = Product.objects.create(name="Milk", barcode="12345670", selling_price=Decimal("1000.00"))
        self.session = StocktakeSession.objects.create(name="Opening Count", created_by=self.admin)
        self.zone = StocktakeZone.objects.create(session=self.session, name="Shelf A")
        self.zone.assigned_users.add(self.clerk)

    def test_draft_count_does_not_change_sellable_stock(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)
        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode,
            "good_quantity": 12,
            "damaged_quantity": 2,
            "expired_quantity": 1,
            "reserved_quantity": 3,
        })
        self.assertEqual(response.status_code, 200)
        count = StocktakeCount.objects.get()
        self.assertEqual(count.good_quantity, 12)
        self.assertEqual(count.counted_by, self.clerk)
        self.assertEqual(self.product.stock_on_hand, 0)

    def test_unassigned_clerk_cannot_count_zone(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.other)
        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode,
            "good_quantity": 5,
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(StocktakeCount.objects.exists())

    def test_apply_combines_zones_and_sets_opening_balance_once(self):
        second_zone = StocktakeZone.objects.create(session=self.session, name="Store Room", is_complete=True)
        second_zone.assigned_users.add(self.clerk)
        self.zone.is_complete = True
        self.zone.save(update_fields=["is_complete"])
        StocktakeCount.objects.create(
            session=self.session, zone=self.zone, product=self.product,
            good_quantity=8, approved_good_quantity=8,
            counted_by=self.clerk, approved_by=self.admin,
            status=StocktakeCount.STATUS_APPROVED,
        )
        StocktakeCount.objects.create(
            session=self.session, zone=second_zone, product=self.product,
            good_quantity=17, approved_good_quantity=17,
            counted_by=self.clerk, approved_by=self.admin,
            status=StocktakeCount.STATUS_APPROVED,
        )
        self.session.status = StocktakeSession.STATUS_REVIEW
        self.session.save(update_fields=["status"])
        self.client.force_login(self.admin)
        response = self.client.post(reverse("stocktake_apply", args=[self.session.pk]))
        self.assertRedirects(response, reverse("stocktake_detail", args=[self.session.pk]))
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, StocktakeSession.STATUS_APPLIED)
        self.assertEqual(self.product.stock_on_hand, 25)
        movement = StockMovement.objects.get(product=self.product)
        self.assertEqual(movement.quantity, 25)
        self.assertEqual(movement.actor, self.admin)
        second = self.client.post(reverse("stocktake_apply", args=[self.session.pk]))
        self.assertEqual(second.status_code, 404)
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 1)

    def test_unknown_barcode_can_be_created_during_count(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)
        response = self.client.post(reverse("stocktake_quick_product", args=[self.zone.pk]), {
            "barcode": "99887766",
            "name": "New Soap",
            "variant": "100g",
            "category": "Toiletries",
            "selling_price": "750.00",
            "cost_price": "600.00",
            "reorder_level": "4",
            "good_quantity": "18",
            "damaged_quantity": "2",
            "expired_quantity": "1",
            "reserved_quantity": "3",
            "note": "Opening shelf count",
        })
        self.assertEqual(response.status_code, 200)
        product = Product.objects.get(barcode="99887766")
        self.assertEqual(product.name, "New Soap")
        self.assertEqual(product.category.name, "Toiletries")
        self.assertEqual(product.reorder_level, 4)
        count = StocktakeCount.objects.get(product=product, zone=self.zone)
        self.assertEqual(count.good_quantity, 18)
        self.assertEqual(count.damaged_quantity, 2)
        self.assertEqual(count.expired_quantity, 1)
        self.assertEqual(count.reserved_quantity, 3)
        self.assertEqual(count.note, "Opening shelf count")
        self.assertEqual(product.stock_on_hand, 0)

    def test_product_can_be_created_without_physical_barcode(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)
        response = self.client.post(reverse("stocktake_quick_product", args=[self.zone.pk]), {
            "barcode": "",
            "name": "Loose Sugar",
            "variant": "1kg",
            "category": "Groceries",
            "selling_price": "1800.00",
            "cost_price": "1500.00",
            "good_quantity": "11",
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        product = Product.objects.get(name="Loose Sugar")
        self.assertTrue(product.barcode.startswith("MANUAL-"))
        self.assertEqual(payload["barcode_display"], "No barcode")
        self.assertFalse(payload["has_barcode"])
        count = StocktakeCount.objects.get(product=product, zone=self.zone)
        self.assertEqual(count.good_quantity, 11)

    def test_count_page_suggests_previous_product_values(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        category = Category.objects.create(name="Beverages")
        Product.objects.create(
            name="Milo",
            barcode="55500011",
            variant="500g",
            category=category,
            selling_price=Decimal("2500.00"),
        )
        self.client.force_login(self.clerk)
        response = self.client.get(reverse("stocktake_count_zone", args=[self.zone.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="product-name-suggestions"')
        self.assertContains(response, 'value="Milo"')
        self.assertContains(response, 'id="variant-suggestions"')
        self.assertContains(response, 'value="500g"')
        self.assertContains(response, 'id="category-suggestions"')
        self.assertContains(response, 'value="Beverages"')

    def test_mobile_count_page_contains_camera_scanner_and_manual_fallback(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)
        response = self.client.get(reverse("stocktake_count_zone", args=[self.zone.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BarcodeDetector")
        self.assertContains(response, "Start Camera Scanner")
        self.assertContains(response, "Manual barcode entry", html=False)
