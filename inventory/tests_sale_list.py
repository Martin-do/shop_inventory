from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Sale
from .tests import grant


class SaleListTests(TestCase):
    """A completed sale must be reachable again later, not just right after checkout."""

    def setUp(self):
        self.cashier_a = User.objects.create_user("list-cashier-a", password="pw-A-12345")
        self.cashier_b = User.objects.create_user("list-cashier-b", password="pw-B-12345")
        grant(self.cashier_a, "access_pos", "view_own_sales")
        grant(self.cashier_b, "access_pos", "view_own_sales")
        self.sale_a = Sale.objects.create(cashier=self.cashier_a, cashier_name="list-cashier-a", total=Decimal("500.00"), amount_paid=Decimal("500.00"))
        self.sale_b = Sale.objects.create(cashier=self.cashier_b, cashier_name="list-cashier-b", total=Decimal("700.00"), amount_paid=Decimal("700.00"))

    def test_cashier_sees_only_their_own_sales(self):
        self.client.force_login(self.cashier_a)
        response = self.client.get(reverse("sale_list"))
        receipts = [sale.pk for sale in response.context["page_obj"]]
        self.assertIn(self.sale_a.pk, receipts)
        self.assertNotIn(self.sale_b.pk, receipts)

    def test_view_all_sales_sees_everyone(self):
        manager = User.objects.create_user("list-manager", password="pw-Mgr-12345")
        grant(manager, "view_all_sales")
        self.client.force_login(manager)
        response = self.client.get(reverse("sale_list"))
        receipts = [sale.pk for sale in response.context["page_obj"]]
        self.assertIn(self.sale_a.pk, receipts)
        self.assertIn(self.sale_b.pk, receipts)

    def test_account_without_any_sales_permission_is_redirected(self):
        counter = User.objects.create_user("list-counter", password="pw-Count-12345")
        grant(counter, "view_products")
        self.client.force_login(counter)
        self.assertEqual(self.client.get(reverse("sale_list")).status_code, 302)

    def test_receipt_is_reachable_from_the_list(self):
        self.client.force_login(self.cashier_a)
        response = self.client.get(reverse("sale_list"))
        self.assertContains(response, reverse("sale_receipt", args=[self.sale_a.pk]))

    def test_search_filters_by_receipt_and_status(self):
        self.sale_b.status = Sale.STATUS_REVERTED
        self.sale_b.save(update_fields=["status"])
        manager = User.objects.create_user("list-manager-2", password="pw-Mgr-12345")
        grant(manager, "view_all_sales")
        self.client.force_login(manager)

        response = self.client.get(reverse("sale_list"), {"q": "list-cashier-a"})
        receipts = [sale.pk for sale in response.context["page_obj"]]
        self.assertEqual(receipts, [self.sale_a.pk])

        response = self.client.get(reverse("sale_list"), {"status": Sale.STATUS_REVERTED})
        receipts = [sale.pk for sale in response.context["page_obj"]]
        self.assertEqual(receipts, [self.sale_b.pk])
