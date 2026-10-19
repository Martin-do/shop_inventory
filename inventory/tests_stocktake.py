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

    def _quick_create(self, user, barcode):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.zone.assigned_users.add(user)
        self.client.force_login(user)
        return self.client.post(reverse("stocktake_quick_product", args=[self.zone.pk]), {
            "barcode": barcode, "name": "Priced Item", "selling_price": "500.00",
            "cost_price": "350.00", "reorder_level": "5", "good_quantity": "4",
        })

    def test_counter_without_cost_permission_cannot_set_cost_on_new_product(self):
        from .tests import grant
        counter = User.objects.create_user("no-cost-counter", password="pw")
        grant(counter, "create_products_during_stocktake", "count_assigned_zones", "view_assigned_stocktakes")
        response = self._quick_create(counter, "55500011")
        self.assertEqual(response.status_code, 200)
        product = Product.objects.get(barcode="55500011")
        self.assertEqual(product.selling_price, Decimal("500.00"))
        self.assertEqual(product.cost_price, Decimal("0.00"))

    def test_counter_with_cost_permission_can_set_cost_on_new_product(self):
        from .tests import grant
        counter = User.objects.create_user("cost-counter", password="pw")
        grant(counter, "create_products_during_stocktake", "count_assigned_zones",
              "view_assigned_stocktakes", "change_cost_price")
        response = self._quick_create(counter, "55500022")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Product.objects.get(barcode="55500022").cost_price, Decimal("350.00"))

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
        self.assertContains(response, "function closeCamera()")
        self.assertContains(response, "Camera closed so you can finish the entry.")

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

    def test_rescan_cannot_overwrite_existing_count(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        existing = StocktakeCount.objects.create(
            session=self.session, zone=self.zone, product=self.product,
            good_quantity=5, approved_good_quantity=5, counted_by=self.clerk,
        )
        self.client.force_login(self.clerk)

        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode,
            "good_quantity": "13",
            "damaged_quantity": "1",
            "reserved_quantity": "2",
            "note": "Mistaken second entry",
        })
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["already_counted"])
        self.assertEqual(response.json()["count_id"], existing.pk)
        self.assertEqual(StocktakeCount.objects.count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.good_quantity, 5)
        self.assertEqual(existing.note, "")

    def test_manual_barcode_rescan_flags_existing_and_preserves_no_barcode_label(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        product = Product.objects.create(
            name="Loose Rice", barcode="MANUAL-ABC123",
            selling_price=Decimal("1200.00"),
        )
        StocktakeCount.objects.create(
            session=self.session, zone=self.zone, product=product,
            good_quantity=4, approved_good_quantity=4, counted_by=self.clerk,
        )
        self.client.force_login(self.clerk)
        save = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": product.barcode, "good_quantity": "6",
        })
        self.assertEqual(save.status_code, 409)
        lookup = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": product.barcode, "lookup_only": "1",
        })
        self.assertEqual(lookup.status_code, 200)
        self.assertEqual(lookup.json()["barcode_display"], "No barcode")
        self.assertTrue(lookup.json()["already_counted"])
        self.assertEqual(lookup.json()["existing_count"]["good_quantity"], 4)

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


    def test_barcode_sized_quantity_is_rejected_before_count_is_saved(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)

        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode,
            "good_quantity": "189041857041300",
        })

        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()["barcode_like_quantity"])
        self.assertIn("looks like a barcode scan", response.json()["error"])
        self.assertFalse(StocktakeCount.objects.filter(product=self.product, zone=self.zone).exists())

    def test_barcode_sized_quantity_is_rejected_when_creating_product_in_stocktake(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)

        response = self.client.post(reverse("stocktake_quick_product", args=[self.zone.pk]), {
            "barcode": "6151006000999",
            "name": "Unsafe Count Test",
            "selling_price": "100.00",
            "good_quantity": "161540002401340",
        })

        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()["barcode_like_quantity"])
        self.assertFalse(Product.objects.filter(name="Unsafe Count Test").exists())

    def test_barcode_sized_quantity_is_rejected_when_editing_existing_count(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        count = StocktakeCount.objects.create(
            session=self.session,
            zone=self.zone,
            product=self.product,
            good_quantity=10,
            approved_good_quantity=10,
            counted_by=self.clerk,
        )
        self.client.force_login(self.admin)

        response = self.client.post(reverse("stocktake_edit_record", args=[count.pk]), {
            "name": self.product.name,
            "barcode": self.product.barcode,
            "selling_price": "1000.00",
            "cost_price": "0.00",
            "reorder_level": "5",
            "good_quantity": "136151006000236",
        })

        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()["barcode_like_quantity"])
        count.refresh_from_db()
        self.assertEqual(count.good_quantity, 10)
        self.assertEqual(count.approved_good_quantity, 10)

    def test_stocktake_page_has_browser_side_quantity_guard(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)

        response = self.client.get(reverse("stocktake_count_zone", args=[self.zone.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'max="9999"')
        self.assertContains(response, "maxStocktakeQuantity=9999")
        self.assertContains(response, "Barcode-like input detected in a quantity field")


    def test_pack_counting_calculates_base_units_for_existing_product(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.product.units_per_pack = 12
        self.product.save(update_fields=["units_per_pack"])
        self.client.force_login(self.clerk)

        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode,
            "count_entry_mode": "packs",
            "units_per_pack": "12",
            "pack_count": "5",
            "loose_units": "3",
            "good_quantity": "0",
            "damaged_quantity": "1",
            "expired_quantity": "0",
            "reserved_quantity": "2",
        })

        self.assertEqual(response.status_code, 200)
        count = StocktakeCount.objects.get(product=self.product, zone=self.zone)
        self.assertEqual(count.good_quantity, 63)
        self.assertEqual(count.approved_good_quantity, 63)
        self.assertEqual(count.count_entry_mode, "packs")
        self.assertEqual(count.pack_count_entered, 5)
        self.assertEqual(count.loose_units_entered, 3)
        self.assertEqual(count.units_per_pack_snapshot, 12)
        self.assertEqual(response.json()["quantities"]["good_quantity"], 63)

    def test_new_product_can_define_pack_size_and_opening_count_in_packs(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)

        response = self.client.post(reverse("stocktake_quick_product", args=[self.zone.pk]), {
            "barcode": "6151006999999",
            "name": "Pack Test Product",
            "selling_price": "100.00",
            "cost_price": "80.00",
            "reorder_level": "5",
            "units_per_pack": "10",
            "count_entry_mode": "packs",
            "pack_count": "4",
            "loose_units": "6",
            "good_quantity": "0",
        })

        self.assertEqual(response.status_code, 200)
        product = Product.objects.get(barcode="6151006999999")
        count = StocktakeCount.objects.get(product=product, zone=self.zone)
        self.assertEqual(product.units_per_pack, 10)
        self.assertEqual(count.good_quantity, 46)
        self.assertEqual(count.pack_count_entered, 4)
        self.assertEqual(count.loose_units_entered, 6)
        self.assertEqual(count.units_per_pack_snapshot, 10)

    def test_existing_unit_count_is_not_rewritten_when_pack_size_is_added(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.product.units_per_pack = 12
        self.product.save(update_fields=["units_per_pack"])
        count = StocktakeCount.objects.create(
            session=self.session,
            zone=self.zone,
            product=self.product,
            good_quantity=64,
            approved_good_quantity=64,
            counted_by=self.clerk,
        )
        self.client.force_login(self.clerk)

        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode,
            "lookup_only": "1",
        })

        self.assertEqual(response.status_code, 200)
        count.refresh_from_db()
        self.assertEqual(count.good_quantity, 64)
        self.assertEqual(count.count_entry_mode, "units")
        self.assertIsNone(count.pack_count_entered)
        self.assertEqual(response.json()["existing_count"]["pack_equivalent"], {
            "packs": 5,
            "loose_units": 4,
            "units_per_pack": 12,
            "is_historical_entry": False,
        })

    def test_pack_count_allows_all_loose_units_without_fake_full_packs(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.product.units_per_pack = 12
        self.product.save(update_fields=["units_per_pack"])
        self.client.force_login(self.clerk)
        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode,
            "count_entry_mode": "packs",
            "pack_count": "0",
            "loose_units": "24",
            "good_quantity": "24",
        })
        self.assertEqual(response.status_code, 200)
        count = StocktakeCount.objects.get(product=self.product, zone=self.zone)
        self.assertEqual(count.good_quantity, 24)
        self.assertEqual(count.pack_count_entered, 0)
        self.assertEqual(count.loose_units_entered, 24)

    def test_stocktake_search_returns_units_per_pack(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.product.units_per_pack = 24
        self.product.save(update_fields=["units_per_pack"])
        self.client.force_login(self.clerk)

        response = self.client.get(reverse("stocktake_product_search"), {"q": "Milk"})

        self.assertEqual(response.status_code, 200)
        result = next(item for item in response.json()["results"] if item["id"] == self.product.pk)
        self.assertEqual(result["units_per_pack"], 24)

    def test_stocktake_page_contains_optional_pack_counting_controls(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)

        response = self.client.get(reverse("stocktake_count_zone", args=[self.zone.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "How are you counting the sellable stock?")
        self.assertContains(response, "How many units are in one full pack?")
        self.assertContains(response, "How many full packs?")
        self.assertContains(response, "How many loose units?")
        self.assertContains(response, "TOTAL SELLABLE STOCK")
        self.assertContains(response, 'name="units_per_pack"')
        self.assertContains(response, 'name="pack_count"')
        self.assertContains(response, 'name="loose_units"')
        self.assertContains(response, 'type="hidden" name="count_entry_mode"')
        self.assertContains(response, "Previous count was entered as total units.")


    def test_default_pack_fields_accept_units_without_pack_size(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)
        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode, "count_entry_mode": "units",
            "units_per_pack": "", "pack_count": "0", "loose_units": "17",
            "good_quantity": "17",
        })
        self.assertEqual(response.status_code, 200)
        count = StocktakeCount.objects.get(product=self.product, zone=self.zone)
        self.assertEqual(count.good_quantity, 17)
        self.assertEqual(count.count_entry_mode, "units")
        self.assertIsNone(self.product.units_per_pack)

    def test_default_pack_fields_require_size_for_positive_pack_count(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)
        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode, "count_entry_mode": "units",
            "pack_count": "2", "loose_units": "3", "good_quantity": "3",
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(StocktakeCount.objects.exists())

    def test_counted_product_in_other_zone_requires_explicit_confirmation(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        elsewhere = StocktakeZone.objects.create(session=self.session, name="Storage")
        elsewhere.assigned_users.add(self.clerk)
        StocktakeCount.objects.create(
            session=self.session, zone=elsewhere, product=self.product,
            good_quantity=9, approved_good_quantity=9, counted_by=self.clerk,
        )
        self.client.force_login(self.clerk)

        lookup = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode, "lookup_only": "1",
        })
        self.assertEqual(lookup.json()["other_zone_counts"][0]["zone"], "Storage")
        response = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode, "good_quantity": "6",
        })
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["other_zone_counted"])
        self.assertFalse(StocktakeCount.objects.filter(zone=self.zone).exists())

        allowed = self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode, "good_quantity": "6",
            "confirm_other_zone": "1",
        })
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(StocktakeCount.objects.filter(product=self.product).count(), 2)

    def test_remove_and_restore_stocktake_count_with_audit(self):
        from .stocktake_models import StocktakeRemovedCount, StocktakeEvent
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        count = StocktakeCount.objects.create(
            session=self.session, zone=self.zone, product=self.product,
            good_quantity=64, approved_good_quantity=64, counted_by=self.clerk,
            count_entry_mode="packs", pack_count_entered=5,
            loose_units_entered=4, units_per_pack_snapshot=12,
        )
        self.client.force_login(self.clerk)
        remove = self.client.post(reverse("stocktake_remove_count", args=[count.pk]), {
            "reason": "Duplicate entry confirmed",
        })
        self.assertEqual(remove.status_code, 200)
        self.assertFalse(StocktakeCount.objects.filter(pk=count.pk).exists())
        self.assertEqual(self.session.total_good_units, 0)
        removed = StocktakeRemovedCount.objects.get()
        self.assertEqual(removed.snapshot["good_quantity"], 64)
        self.assertEqual(removed.reason, "Duplicate entry confirmed")
        self.assertTrue(StocktakeEvent.objects.filter(event="count_removed").exists())
        self.assertEqual(self.product.stock_on_hand, 0)

        restore = self.client.post(reverse("stocktake_restore_count", args=[removed.pk]))
        self.assertEqual(restore.status_code, 200)
        restored = StocktakeCount.objects.get()
        self.assertEqual(restored.good_quantity, 64)
        self.assertEqual(restored.pack_count_entered, 5)
        self.assertEqual(restored.units_per_pack_snapshot, 12)
        self.assertEqual(self.session.total_good_units, 64)
        removed.refresh_from_db()
        self.assertEqual(removed.restored_count_id, restored.pk)
        self.assertTrue(StocktakeEvent.objects.filter(event="count_restored").exists())
        self.assertEqual(
            self.client.post(reverse("stocktake_restore_count", args=[removed.pk])).status_code, 409
        )

    def test_cannot_restore_removed_count_if_new_count_exists(self):
        from .stocktake_models import StocktakeRemovedCount
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        original = StocktakeCount.objects.create(
            session=self.session, zone=self.zone, product=self.product,
            good_quantity=8, approved_good_quantity=8, counted_by=self.clerk,
        )
        self.client.force_login(self.clerk)
        self.client.post(reverse("stocktake_remove_count", args=[original.pk]), {
            "reason": "Mistaken count",
        })
        self.client.post(reverse("stocktake_save_count", args=[self.zone.pk]), {
            "barcode": self.product.barcode, "good_quantity": "17",
        })
        removed = StocktakeRemovedCount.objects.get()
        response = self.client.post(reverse("stocktake_restore_count", args=[removed.pk]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(StocktakeCount.objects.get().good_quantity, 17)

    def test_removal_rejects_short_reason_or_finished_zone(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        count = StocktakeCount.objects.create(
            session=self.session, zone=self.zone, product=self.product,
            good_quantity=5, approved_good_quantity=5, counted_by=self.clerk,
        )
        self.client.force_login(self.clerk)
        self.assertEqual(self.client.post(
            reverse("stocktake_remove_count", args=[count.pk]), {"reason": "bad"},
        ).status_code, 400)
        self.zone.is_complete = True
        self.zone.save(update_fields=["is_complete"])
        self.assertEqual(self.client.post(
            reverse("stocktake_remove_count", args=[count.pk]), {"reason": "A proper reason"},
        ).status_code, 409)
        self.assertTrue(StocktakeCount.objects.filter(pk=count.pk).exists())

    def test_staff_cannot_remove_unassigned_zone_count(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        count = StocktakeCount.objects.create(
            session=self.session, zone=self.zone, product=self.product,
            good_quantity=5, approved_good_quantity=5, counted_by=self.clerk,
        )
        self.client.force_login(self.other)
        response = self.client.post(reverse("stocktake_remove_count", args=[count.pk]), {
            "reason": "Wrong product",
        })
        self.assertIn(response.status_code, [302, 403])
        self.assertTrue(StocktakeCount.objects.filter(pk=count.pk).exists())

    def test_stocktake_new_product_warns_for_matching_name_variant(self):
        self.session.status = StocktakeSession.STATUS_COUNTING
        self.session.save(update_fields=["status"])
        self.client.force_login(self.clerk)
        url = reverse("stocktake_quick_product", args=[self.zone.pk])
        response = self.client.post(url, {
            "name": "  Milk  ", "barcode": "",
            "good_quantity": "4", "count_entry_mode": "units",
        })
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["similar_products"])
        self.assertEqual(Product.objects.count(), 1)

        # Staff may explicitly confirm genuinely distinct products.
        confirmed = self.client.post(url, {
            "name": "Milk", "barcode": "", "good_quantity": "4",
            "count_entry_mode": "units", "confirm_similar_product": "1",
        })
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(Product.objects.count(), 2)


class UnpricedStocktakeGateTests(OpeningStocktakeTests):
    def test_cannot_approve_or_apply_while_product_has_no_price(self):
        self.product.selling_price = Decimal("0.00")
        self.product.save()
        count = StocktakeCount.objects.create(
            session=self.session, zone=self.zone, product=self.product,
            good_quantity=4, counted_by=self.clerk,
        )
        self.session.status = StocktakeSession.STATUS_REVIEW
        self.session.save(update_fields=["status"])
        self.client.force_login(self.admin)
        self.client.post(reverse("stocktake_review_count", args=[count.pk]), {"action": "approve", "approved_good_quantity": "4"})
        count.refresh_from_db()
        self.assertNotEqual(count.status, StocktakeCount.STATUS_APPROVED)
        count.status = StocktakeCount.STATUS_APPROVED
        count.approved_good_quantity = 4
        count.save()
        self.client.post(reverse("stocktake_apply", args=[self.session.pk]))
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, StocktakeSession.STATUS_REVIEW)
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 0)
