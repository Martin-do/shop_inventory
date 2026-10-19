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
        response = self.client.get(reverse("price_entry"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([p["name"] for p in response.context["queue"]], ["Indomie"])
        self.assertContains(response, "Save &amp; next")

    def test_anonymous_cannot_view_or_save(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("price_entry")).status_code, 302)
        self.assertEqual(self.post(self.unpriced, selling_price="100").status_code, 401)
        self.assertEqual(self.client.get(reverse("price_entry_search"), {"q": "milk"}).status_code, 401)
        self.unpriced.refresh_from_db()
        self.assertEqual(self.unpriced.selling_price, 0)

    def test_page_requires_price_permission(self):
        clerk = User.objects.create_user("viewer", password="pw")
        grant(clerk, "view_products")
        self.client.force_login(clerk)
        self.assertNotEqual(self.client.get(reverse("price_entry")).status_code, 200)
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
        history = self.client.get(reverse("price_entry")).context["history"]
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
        item = self.client.get(reverse("price_entry")).context["queue"][0]
        self.assertNotIn("cost_price", item)
        grant(self.pricer, "view_cost_price")
        item = self.client.get(reverse("price_entry")).context["queue"][0]
        self.assertEqual(item["cost_price"], "80.00")
