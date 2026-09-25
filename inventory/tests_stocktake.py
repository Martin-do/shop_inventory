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

    def test_count_page_has_live_typeahead_controls(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)
        response = self.client.get(reverse("stocktake_count_zone", args=[self.zone.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-suggest-field="name"')
        self.assertContains(response, 'data-suggest-field="variant"')
        self.assertContains(response, 'data-suggest-field="category"')
        self.assertContains(response, 'data-suggest-autofill="product-details"')
        self.assertGreaterEqual(response.content.decode().count('data-suggest-autofill="product-details"'), 2)
        self.assertContains(response, "autofillSuggestedProduct")
        self.assertContains(response, "Related product details filled automatically")
        self.assertContains(response, reverse("stocktake_product_search"))

    def test_mobile_count_page_contains_camera_scanner_and_manual_fallback(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)
        response = self.client.get(reverse("stocktake_count_zone", args=[self.zone.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BarcodeDetector")
        self.assertContains(response, "Start Camera Scanner")
        self.assertContains(response, "Manual barcode entry", html=False)
        self.assertContains(response, 'id="lookup-suggest-menu"')
        self.assertContains(response, "Scan barcode or type product name")

    def test_existing_count_is_returned_for_editing(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        count = StocktakeCount.objects.create(
            session=self.session,
            zone=self.zone,
            product=self.product,
            good_quantity=9,
            damaged_quantity=2,
            expired_quantity=1,
            reserved_quantity=3,
            approved_good_quantity=9,
            note="Needs recount",
            counted_by=self.clerk,
        )
        self.client.force_login(self.clerk)
        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode,
            "lookup_only": "1",
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["existing_count"]["id"], count.pk)
        self.assertEqual(payload["existing_count"]["good_quantity"], 9)
        self.assertEqual(payload["existing_count"]["damaged_quantity"], 2)
        self.assertEqual(payload["existing_count"]["expired_quantity"], 1)
        self.assertEqual(payload["existing_count"]["reserved_quantity"], 3)
        self.assertEqual(payload["existing_count"]["note"], "Needs recount")

    def test_saving_existing_count_updates_instead_of_creating_duplicate(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        existing = StocktakeCount.objects.create(
            session=self.session,
            zone=self.zone,
            product=self.product,
            good_quantity=5,
            approved_good_quantity=5,
            counted_by=self.clerk,
        )
        self.client.force_login(self.clerk)
        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode,
            "good_quantity": 13,
            "damaged_quantity": 1,
            "expired_quantity": 0,
            "reserved_quantity": 2,
            "note": "Corrected physical count",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(StocktakeCount.objects.count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.good_quantity, 13)
        self.assertEqual(existing.damaged_quantity, 1)
        self.assertEqual(existing.reserved_quantity, 2)
        self.assertEqual(existing.note, "Corrected physical count")
        payload = response.json()
        self.assertEqual(payload["barcode_display"], self.product.barcode)
        self.assertEqual(payload["category"], "")

    def test_manual_barcode_count_update_keeps_no_barcode_display(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        product = Product.objects.create(
            name="Loose Rice",
            barcode="MANUAL-ABC123",
            selling_price=Decimal("1200.00"),
        )
        StocktakeCount.objects.create(
            session=self.session,
            zone=self.zone,
            product=product,
            good_quantity=4,
            approved_good_quantity=4,
            counted_by=self.clerk,
        )
        self.client.force_login(self.clerk)
        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": product.barcode,
            "good_quantity": 6,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["barcode_display"], "No barcode")

    def test_recent_count_has_edit_action(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        StocktakeCount.objects.create(
            session=self.session,
            zone=self.zone,
            product=self.product,
            good_quantity=7,
            approved_good_quantity=7,
            counted_by=self.clerk,
        )
        self.client.force_login(self.clerk)
        response = self.client.get(reverse("stocktake_count_zone", args=[self.zone.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="button edit-count"')
        self.assertContains(response, f'data-barcode="{self.product.barcode}"')
        self.assertContains(response, "Use Edit to reload a saved count")
        self.assertContains(response, 'id="count-search"')
        self.assertContains(response, 'data-search="milk 12345670')

    def test_counted_product_search_has_server_side_fallback(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        second = Product.objects.create(
            name="Golden Penny Sugar",
            barcode="SUGAR-001",
            variant="1kg",
            selling_price=Decimal("1500.00"),
        )
        StocktakeCount.objects.create(
            session=self.session,
            zone=self.zone,
            product=self.product,
            good_quantity=4,
            approved_good_quantity=4,
            counted_by=self.clerk,
        )
        StocktakeCount.objects.create(
            session=self.session,
            zone=self.zone,
            product=second,
            good_quantity=9,
            approved_good_quantity=9,
            counted_by=self.clerk,
        )
        self.client.force_login(self.clerk)
        response = self.client.get(
            reverse("stocktake_count_zone", args=[self.zone.pk]),
            {"q": "sugar"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Golden Penny Sugar")
        self.assertNotContains(response, ">Milk<", html=False)
        self.assertEqual(response.context["count_search_query"], "sugar")
        self.assertEqual(len(response.context["recent"]), 1)

    def test_counted_product_search_includes_counts_beyond_first_thirty(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        for index in range(35):
            product = Product.objects.create(
                name=f"Search Item {index:02d}",
                barcode=f"SEARCH{index:02d}",
                selling_price=Decimal("100.00"),
            )
            StocktakeCount.objects.create(
                session=self.session,
                zone=self.zone,
                product=product,
                good_quantity=index + 1,
                approved_good_quantity=index + 1,
                counted_by=self.clerk,
            )

        self.client.force_login(self.clerk)
        response = self.client.get(reverse("stocktake_count_zone", args=[self.zone.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search Item 00")
        self.assertContains(response, "Search Item 34")
        self.assertEqual(len(response.context["recent"]), 35)

    def test_full_record_edit_updates_product_and_count_without_touching_live_stock(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.started_at = self.session.created_at
        self.session.save(update_fields=["status", "started_at"])
        category = Category.objects.create(name="Old Category")
        self.product.category = category
        self.product.cost_price = Decimal("700.00")
        self.product.save()
        count = StocktakeCount.objects.create(
            session=self.session,
            zone=self.zone,
            product=self.product,
            good_quantity=5,
            approved_good_quantity=5,
            counted_by=self.clerk,
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse("stocktake_edit_record", args=[count.pk]), {
            "name": "Fresh Milk",
            "barcode": "12345671",
            "variant": "500ml",
            "category": "Dairy",
            "selling_price": "1250.00",
            "cost_price": "900.00",
            "reorder_level": "8",
            "good_quantity": "14",
            "damaged_quantity": "2",
            "expired_quantity": "1",
            "reserved_quantity": "3",
            "note": "Corrected full record",
        })
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        count.refresh_from_db()
        self.assertEqual(self.product.name, "Fresh Milk")
        self.assertEqual(self.product.barcode, "12345671")
        self.assertEqual(self.product.variant, "500ml")
        self.assertEqual(self.product.category.name, "Dairy")
        self.assertEqual(self.product.selling_price, Decimal("1250.00"))
        self.assertEqual(self.product.cost_price, Decimal("900.00"))
        self.assertEqual(self.product.reorder_level, 8)
        self.assertEqual(count.good_quantity, 14)
        self.assertEqual(count.damaged_quantity, 2)
        self.assertEqual(count.expired_quantity, 1)
        self.assertEqual(count.reserved_quantity, 3)
        self.assertEqual(count.note, "Corrected full record")
        self.assertEqual(self.product.stock_on_hand, 0)

    def test_full_record_edit_rejects_duplicate_barcode(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        Product.objects.create(name="Other", barcode="DUPLICATE", selling_price=Decimal("10.00"))
        count = StocktakeCount.objects.create(
            session=self.session,
            zone=self.zone,
            product=self.product,
            good_quantity=5,
            approved_good_quantity=5,
            counted_by=self.clerk,
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse("stocktake_edit_record", args=[count.pk]), {
            "name": self.product.name,
            "barcode": "DUPLICATE",
            "selling_price": "1000.00",
            "cost_price": "0.00",
            "reorder_level": "5",
            "good_quantity": "5",
        })
        self.assertEqual(response.status_code, 409)
        self.product.refresh_from_db()
        self.assertEqual(self.product.barcode, "12345670")

    def test_lookup_flags_manual_product_page_adjustment_during_stocktake(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        count = StocktakeCount.objects.create(
            session=self.session,
            zone=self.zone,
            product=self.product,
            good_quantity=12,
            approved_good_quantity=12,
            counted_by=self.clerk,
        )
        StockMovement.objects.create(
            product=self.product,
            movement_type=StockMovement.ADJUSTMENT,
            quantity=9,
            note="Manual adjustment: 0 → 9 (corrected from Products page)",
            actor=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode,
            "lookup_only": "1",
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["existing_count"]["id"], count.pk)
        self.assertEqual(payload["live_stock"], 9)
        self.assertIsNotNone(payload["manual_adjustment_during_stocktake"])
        self.assertIn("Manual adjustment:", payload["manual_adjustment_during_stocktake"]["note"])

    def test_stocktake_search_suggests_from_first_character(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        category = Category.objects.create(name="Beverages")
        Product.objects.create(
            name="Milo",
            barcode="55500011",
            variant="500g",
            category=category,
            selling_price=Decimal("2500.00"),
            cost_price=Decimal("2100.00"),
        )
        self.client.force_login(self.clerk)
        response = self.client.get(reverse("stocktake_product_search"), {"q": "M"})
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        result = next(item for item in results if item["name"] == "Milo")
        self.assertEqual(result["variant"], "500g")
        self.assertEqual(result["category"], "Beverages")
        self.assertEqual(result["selling_price"], "2500.00")
        self.assertEqual(result["cost_price"], "2100.00")
        self.assertEqual(result["reorder_level"], 5)
        self.assertEqual(result["barcode_display"], "55500011")
