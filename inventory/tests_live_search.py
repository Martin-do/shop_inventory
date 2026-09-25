from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Product, UserProfile
from .stocktake_models import StocktakeCount, StocktakeSession, StocktakeZone


class LiveSearchUiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="live-search-admin",
            email="admin@example.com",
            password="pass12345",
        )
        UserProfile.objects.get_or_create(
            user=self.user,
            defaults={"role": UserProfile.ROLE_ADMIN},
        )
        self.client.force_login(self.user)

    def test_shared_live_search_script_is_loaded(self):
        response = self.client.get(reverse("product_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "inventory/live_search.js")

    def test_product_customer_sales_and_audit_searches_are_live(self):
        cases = [
            ("product_list", "#product-search-results"),
            ("customer_list", "#customer-search-results"),
            ("sale_list", "#sale-search-results"),
            ("audit_history", "#audit-search-results"),
        ]
        for route_name, target in cases:
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "data-live-search")
                self.assertContains(response, f'data-live-target="{target}"')
                self.assertContains(response, f'id="{target[1:]}"')

    def test_stocktake_counted_products_search_updates_live(self):
        session = StocktakeSession.objects.create(
            name="Opening Count",
            created_by=self.user,
            status=StocktakeSession.STATUS_COUNTING,
        )
        zone = StocktakeZone.objects.create(session=session, name="Inner")
        product = Product.objects.create(
            name="Searchable Item",
            barcode="LIVE-SEARCH-001",
            selling_price="100.00",
        )
        StocktakeCount.objects.create(
            session=session,
            zone=zone,
            product=product,
            good_quantity=3,
            approved_good_quantity=3,
            counted_by=self.user,
        )

        response = self.client.get(reverse("stocktake_count_zone", args=[zone.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="count-search-form"')
        self.assertContains(response, "data-live-search")
        self.assertContains(response, 'data-live-target="#counted-products-results"')
        self.assertContains(response, 'id="counted-products-results"')
        self.assertContains(response, "Results update as you type")
