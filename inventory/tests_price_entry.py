from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import AuditLog, Product
from .tests import grant

User = get_user_model()


class PriceEntryTests(TestCase):
    def setUp(self):
        self.pricer = User.objects.create_user("pricer", password="pw")
        grant(self.pricer, "change_selling_price", "view_products")
        self.pricer = User.objects.get(pk=self.pricer.pk)
        self.client.force_login(self.pricer)
        self.unpriced = Product.objects.create(name="Indomie", barcode="MANUAL-1", selling_price=0)
        self.priced = Product.objects.create(name="Milk", barcode="9001", selling_price=500)
        self.inactive = Product.objects.create(name="Old", barcode="9002", selling_price=0, is_active=False)

    def post(self, product, **data):
        return self.client.post(reverse("price_entry_save"), {"product_id": product.pk, **data})

    def test_page_queue_only_has_active_unpriced_products(self):
        response = self.client.get(reverse("price_entry"), {"scope": "all"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p["name"] for p in response.context["queue"]], ["Indomie"])
        self.assertContains(response, "Save &amp; next")

    def test_anonymous_cannot_view_or_save(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("price_entry"), {"scope": "all"}).status_code, 302)
        self.assertEqual(self.post(self.unpriced, selling_price="100").status_code, 401)
        self.assertEqual(self.client.get(reverse("price_entry_search"), {"q": "milk"}).status_code, 401)
        self.unpriced.refresh_from_db()
        self.assertEqual(self.unpriced.selling_price, 0)

    def test_page_requires_price_permission(self):
        clerk = User.objects.create_user("viewer", password="pw")
        grant(clerk, "view_products")
        self.client.force_login(clerk)
        self.assertNotEqual(self.client.get(reverse("price_entry"), {"scope": "all"}).status_code, 200)
        self.assertNotEqual(self.post(self.unpriced, selling_price="100").status_code, 200)
        self.unpriced.refresh_from_db()
        self.assertEqual(self.unpriced.selling_price, 0)

    def test_save_unit_price_is_recorded_as_audited_edit(self):
        response = self.post(self.unpriced, selling_price="150.5")
        self.assertEqual(response.status_code, 200)
        self.unpriced.refresh_from_db()
        self.assertEqual(self.unpriced.selling_price, Decimal("150.50"))
        log = AuditLog.objects.filter(object_type="inventory.product", object_id=str(self.unpriced.pk), action="update").first()
        self.assertEqual(log.actor, self.pricer)
        self.assertIn("selling_price", log.metadata["changes"])
        self.assertEqual(response.json()["history"]["by"], "pricer")

    def test_pack_price_requires_units_per_pack(self):
        response = self.post(self.unpriced, selling_price="100", pack_selling_price="1000")
        self.assertEqual(response.status_code, 400)
        self.unpriced.refresh_from_db()
        self.assertEqual(self.unpriced.selling_price, 0)

    def test_save_with_pack(self):
        response = self.post(self.unpriced, selling_price="100", units_per_pack="12", pack_selling_price="1100")
        self.assertEqual(response.status_code, 200)
        self.unpriced.refresh_from_db()
        self.assertEqual(self.unpriced.units_per_pack, 12)
        self.assertEqual(self.unpriced.pack_selling_price, Decimal("1100.00"))

    def test_pack_price_is_optional(self):
        self.post(self.unpriced, selling_price="100", units_per_pack="6")
        self.unpriced.refresh_from_db()
        self.assertEqual(self.unpriced.units_per_pack, 6)
        self.assertIsNone(self.unpriced.pack_selling_price)
        self.assertEqual(self.unpriced.effective_pack_selling_price, Decimal("600.00"))

    def test_rejects_zero_negative_and_junk_prices(self):
        for bad in ["", "0", "-5", "abc", "NaN", "99999999999"]:
            self.assertEqual(self.post(self.unpriced, selling_price=bad).status_code, 400, bad)
        self.assertEqual(self.post(self.unpriced, selling_price="5", units_per_pack="1").status_code, 400)
        self.unpriced.refresh_from_db()
        self.assertEqual(self.unpriced.selling_price, 0)

    def test_editing_an_existing_price_and_history(self):
        self.post(self.priced, selling_price="650")
        self.priced.refresh_from_db()
        self.assertEqual(self.priced.selling_price, Decimal("650.00"))
        history = self.client.get(reverse("price_entry"), {"scope": "all"}).context["history"]
        self.assertEqual(history[0]["label"], "Milk")
        self.assertEqual(history[0]["selling_price"], "650.00")

    def test_search_finds_priced_and_unpriced(self):
        data = self.client.get(reverse("price_entry_search"), {"q": "mil"}).json()
        self.assertEqual([r["name"] for r in data["results"]], ["Milk"])
        self.assertTrue(data["results"][0]["priced"])
        self.assertEqual(self.client.get(reverse("price_entry_search"), {"q": "m"}).json()["results"], [])

    def test_cost_price_hidden_without_permission(self):
        self.unpriced.cost_price = Decimal("80")
        self.unpriced.save()
        item = self.client.get(reverse("price_entry"), {"scope": "all"}).context["queue"][0]
        self.assertNotIn("cost_price", item)
        grant(self.pricer, "view_cost_price")
        item = self.client.get(reverse("price_entry"), {"scope": "all"}).context["queue"][0]
        self.assertEqual(item["cost_price"], "80.00")


class PriceGateTests(TestCase):
    def setUp(self):
        from .models import StockReceipt, StockReceiptLine
        from .stocktake_models import StocktakeSession, StocktakeZone, StocktakeCount
        self.boss = User.objects.create_user("boss", password="pw", is_superuser=True, is_staff=True)
        self.client.force_login(self.boss)
        self.product = Product.objects.create(name="Unpriced", barcode="G1", selling_price=0)
        self.other = Product.objects.create(name="Loose", barcode="G2", selling_price=0)
        self.receipt = StockReceipt.objects.create(reference="R1", status=StockReceipt.STATUS_SUBMITTED, created_by=self.boss)
        self.line = StockReceiptLine.objects.create(receipt=self.receipt, product=self.product, quantity_received=3, entered_by=self.boss)

    def test_receipt_line_cannot_be_approved_without_price(self):
        self.client.post(reverse("stock_receipt_review_line", args=[self.receipt.pk, self.line.pk]), {"approved_quantity": "3"})
        self.line.refresh_from_db()
        self.assertEqual(self.line.status, "pending")
        self.product.selling_price = 100
        self.product.save()
        self.client.post(reverse("stock_receipt_review_line", args=[self.receipt.pk, self.line.pk]), {"approved_quantity": "3"})
        self.line.refresh_from_db()
        self.assertEqual(self.line.status, "approved")

    def test_default_queue_only_has_pending_stock(self):
        queue = self.client.get(reverse("price_entry")).context["queue"]
        self.assertEqual([p["name"] for p in queue], ["Unpriced"])
        queue = self.client.get(reverse("price_entry"), {"scope": "all"}).context["queue"]
        self.assertEqual(len(queue), 2)


class PriceEntryConcurrencyTests(TestCase):
    def setUp(self):
        from django.test import Client
        self.user = User.objects.create_user("shared", password="pw")
        grant(self.user, "change_selling_price", "view_products")
        self.user = User.objects.get(pk=self.user.pk)
        self.phone_a, self.phone_b = Client(), Client()
        self.phone_a.force_login(self.user)
        self.phone_b.force_login(self.user)  # same account, separate session
        for n in range(25):
            Product.objects.create(name=f"Item {n:02d}", barcode=f"MANUAL-{n}", selling_price=0)

    def ids(self, client, **params):
        params.setdefault("scope", "all")
        return [p["id"] for p in client.get(reverse("price_entry"), params).context["queue"]]

    def test_two_phones_on_one_account_get_different_products(self):
        a, b = self.ids(self.phone_a), self.ids(self.phone_b)
        self.assertEqual(len(a), 10)
        self.assertEqual(len(b), 10)
        self.assertFalse(set(a) & set(b))

    def test_reload_resumes_same_products(self):
        first = self.ids(self.phone_a)
        self.assertEqual(self.ids(self.phone_a), first)

    def test_next_batch_excludes_other_phones_and_seen_items(self):
        a = self.ids(self.phone_a)
        self.ids(self.phone_b)
        data = self.phone_a.get(reverse("price_entry_next"), {"scope": "all", "exclude": ",".join(map(str, a))}).json()
        got = [i["id"] for i in data["items"]]
        self.assertEqual(len(got), 5)  # 25 total - 10 (a) - 10 (b)
        self.assertFalse(set(got) & set(a))

    def test_saving_something_already_priced_returns_conflict_then_confirm_overwrites(self):
        product = Product.objects.order_by("id").first()
        save = lambda client, **extra: client.post(reverse("price_entry_save"), {"product_id": product.pk, "selling_price": "100", "seen_price": "", **extra})
        self.assertEqual(save(self.phone_a).status_code, 200)
        second = save(self.phone_b, selling_price="150")
        self.assertEqual(second.status_code, 409)
        body = second.json()
        self.assertTrue(body["conflict"])
        self.assertEqual(body["by"], "shared")
        self.assertEqual(body["product"]["selling_price"], "100.00")
        product.refresh_from_db()
        self.assertEqual(product.selling_price, Decimal("100.00"))
        self.assertEqual(save(self.phone_b, selling_price="150", confirm="1").status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.selling_price, Decimal("150.00"))

    def test_anonymous_cannot_claim(self):
        from django.test import Client
        self.assertEqual(Client().get(reverse("price_entry_next")).status_code, 401)


class PackSizePrefillTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("p", password="pw", is_superuser=True, is_staff=True)
        self.client.force_login(self.user)

    def test_pack_size_from_stocktake_count_prefills_when_product_has_none(self):
        from .stocktake_models import StocktakeSession, StocktakeZone, StocktakeCount
        product = Product.objects.create(name="Water", barcode="W1", selling_price=0)
        session = StocktakeSession.objects.create(name="S", created_by=self.user)
        zone = StocktakeZone.objects.create(session=session, name="Z")
        StocktakeCount.objects.create(session=session, zone=zone, product=product, good_quantity=24,
                                      units_per_pack_snapshot=12, counted_by=self.user)
        item = self.client.get(reverse("price_entry"), {"scope": "all"}).context["queue"][0]
        self.assertEqual(item["units_per_pack"], 12)

    def test_stays_empty_when_no_pack_size_recorded(self):
        Product.objects.create(name="Single", barcode="S1", selling_price=0)
        item = self.client.get(reverse("price_entry"), {"scope": "all"}).context["queue"][0]
        self.assertEqual(item["units_per_pack"], "")
