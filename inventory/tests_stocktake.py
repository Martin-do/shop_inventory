from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Product, StockMovement, UserProfile
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
        })
        self.assertEqual(response.status_code, 200)
        product = Product.objects.get(barcode="99887766")
        self.assertEqual(product.name, "New Soap")
        self.assertEqual(product.category.name, "Toiletries")

    def test_mobile_count_page_contains_camera_scanner_and_manual_fallback(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)
        response = self.client.get(reverse("stocktake_count_zone", args=[self.zone.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BarcodeDetector")
        self.assertContains(response, "Start Camera Scanner")
        self.assertContains(response, "Manual barcode entry", html=False)
