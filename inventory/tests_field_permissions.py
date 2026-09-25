import io
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .access_control import PRESETS, has_access
from .models import Product, StockMovement, UserProfile
from .stocktake_models import StocktakeCount, StocktakeSession, StocktakeZone
from .permission_forms import StaffAccessForm
from .tests import grant, make_product


def staff_from_preset(owner, username, preset):
    ids = [
        str(p.pk)
        for p in Permission.objects.filter(
            content_type__app_label="inventory", codename__in=PRESETS[preset]["permissions"]
        )
    ]
    form = StaffAccessForm(
        {"username": username, "password": "pw-Staff-12345", "is_active": "on", "preset": preset, "permissions": ids},
        actor=owner,
    )
    assert form.is_valid(), form.errors
    return form.save()


class PosLookupAccessTests(TestCase):
    """The checkout screen must be able to read the catalogue it sells from."""

    def setUp(self):
        self.owner = User.objects.create_superuser("fp-owner", "", "pw-Owner-12345")
        make_product(barcode="7001", stock=3)

    def test_cashier_presets_can_load_the_catalogue_and_search(self):
        for preset in ("cashier", "senior_cashier"):
            user = staff_from_preset(self.owner, f"till-{preset}", preset)
            self.client.force_login(user)
            catalog = self.client.get(reverse("api_active_catalog"))
            search = self.client.get(reverse("api_product_search"), {"q": "Item"})
            self.assertEqual(catalog.status_code, 200, preset)
            self.assertEqual(search.status_code, 200, preset)
            self.assertEqual(len(catalog.json()["products"]), 1)

    def test_catalogue_stays_closed_to_accounts_without_pos_or_product_access(self):
        user = staff_from_preset(self.owner, "customers-only", "custom")
        grant(user, "view_customers")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("api_active_catalog")).status_code, 403)

    def test_dashboard_redirect_for_a_cashier_shows_no_error(self):
        user = staff_from_preset(self.owner, "till-home", "cashier")
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"), follow=True)
        self.assertEqual(response.redirect_chain[-1][0], reverse("pos"))
        self.assertEqual([str(m) for m in response.context["messages"]], [])


class ProductFieldPermissionTests(TestCase):
    def setUp(self):
        self.product = make_product(barcode="7100", selling_price="100.00", stock=10)
        self.product.cost_price = Decimal("60.00")
        self.product.save()
        self.user = User.objects.create_user("fp-editor", password="pw-Editor-12345")
        grant(self.user, "view_products", "edit_products")
        self.client.force_login(self.user)
        self.url = reverse("product_update", args=[self.product.pk])

    def _post(self, **overrides):
        data = {"name": "Renamed", "barcode": "7100", "selling_price": "100.00", "reorder_level": 5, "is_active": "on"}
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_price_and_cost_are_protected_without_permission(self):
        form = self.client.get(self.url).context["form"]
        self.assertTrue(form.fields["selling_price"].disabled)
        self.assertNotIn("cost_price", form.fields)

        self._post(selling_price="1.00", cost_price="1.00")
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Renamed")
        self.assertEqual(self.product.selling_price, Decimal("100.00"))
        self.assertEqual(self.product.cost_price, Decimal("60.00"))

    def test_selling_price_can_change_with_permission(self):
        grant(self.user, "change_selling_price")
        self._post(selling_price="150.00")
        self.product.refresh_from_db()
        self.assertEqual(self.product.selling_price, Decimal("150.00"))

    def test_cost_price_is_visible_but_locked_with_view_only(self):
        grant(self.user, "view_cost_price")
        form = self.client.get(self.url).context["form"]
        self.assertTrue(form.fields["cost_price"].disabled)
        self._post(cost_price="5.00")
        self.product.refresh_from_db()
        self.assertEqual(self.product.cost_price, Decimal("60.00"))

    def test_stock_cannot_be_adjusted_without_permission(self):
        form = self.client.get(self.url).context["form"]
        self.assertNotIn("total_stock", form.fields)
        self._post(total_stock=999, adjustment_note="Trying to inflate stock")
        self.assertEqual(self.product.stock_on_hand, 10)

    def test_stock_adjustment_works_with_permission_and_reason(self):
        grant(self.user, "adjust_stock")
        self._post(total_stock=12, adjustment_note="Recount after audit")
        self.assertEqual(self.product.stock_on_hand, 12)
        movement = StockMovement.objects.filter(movement_type=StockMovement.ADJUSTMENT).latest("id")
        self.assertEqual(movement.actor, self.user)

    def test_active_stocktake_locks_product_page_total_stock_but_keeps_metadata_editable(self):
        grant(self.user, "adjust_stock")
        session = StocktakeSession.objects.create(
            name="Live Count",
            created_by=self.user,
            status=StocktakeSession.STATUS_COUNTING,
        )
        zone = StocktakeZone.objects.create(session=session, name="Shelf")
        StocktakeCount.objects.create(
            session=session,
            zone=zone,
            product=self.product,
            good_quantity=13,
            approved_good_quantity=13,
            counted_by=self.user,
        )

        form = self.client.get(self.url).context["form"]
        self.assertTrue(form.fields["total_stock"].disabled)
        response = self._post(
            name="Still editable",
            total_stock=99,
            adjustment_note="Trying product-page stock correction",
        )
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Still editable")
        self.assertEqual(self.product.stock_on_hand, 10)
        self.assertEqual(
            StockMovement.objects.filter(product=self.product, movement_type=StockMovement.ADJUSTMENT).count(),
            0,
        )

    def test_new_product_cost_is_ignored_without_change_cost_permission(self):
        grant(self.user, "create_products")
        self.client.post(reverse("product_create"), {
            "name": "Fresh item", "barcode": "7200", "selling_price": "20.00",
            "cost_price": "15.00", "reorder_level": 5,
        })
        created = Product.objects.get(barcode="7200")
        self.assertEqual(created.cost_price, Decimal("0.00"))


class ReceiveStockPermissionTests(TestCase):
    def setUp(self):
        from .models import StockReceipt

        self.user = User.objects.create_user("fp-receiver", password="pw-Receive-12345")
        grant(self.user, "receive_stock")
        self.client.force_login(self.user)
        self.receipt = StockReceipt.objects.create(
            supplier="Test supplier",
            reference="FP-RECEIPT",
            created_by=self.user,
        )

    def _receive_unknown(self):
        return self.client.post(reverse("stock_receipt_add_line", args=[self.receipt.pk]), {
            "barcode": "7300",
            "quantity": 4,
            "name": "Unlisted",
            "selling_price": "30.00",
            "cost_price": "20.00",
        })

    def test_unknown_barcode_needs_permission_to_create_products(self):
        self._receive_unknown()
        self.assertFalse(Product.objects.filter(barcode="7300").exists())
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_unknown_barcode_is_registered_when_allowed_and_cost_stays_blank(self):
        grant(self.user, "create_products")
        self.client.force_login(self.user)
        self._receive_unknown()
        product = Product.objects.get(barcode="7300")
        self.assertEqual(product.stock_on_hand, 0)
        self.assertEqual(product.cost_price, Decimal("0.00"))
        self.assertEqual(self.receipt.lines.get(product=product).quantity, 4)

    def test_cost_field_is_hidden_without_change_cost_permission(self):
        grant(self.user, "create_products")
        self.client.force_login(self.user)
        response = self.client.get(reverse("stock_receipt_detail", args=[self.receipt.pk]))
        self.assertNotContains(response, 'name="cost_price"')


class ExportAndDashboardVisibilityTests(TestCase):
    def setUp(self):
        make_product(barcode="7400", selling_price="10.00", stock=2)
        self.user = User.objects.create_user("fp-reporter", password="pw-Report-12345")
        self.client.force_login(self.user)

    def _csv_header(self):
        return self.client.get(reverse("export_products_csv")).content.decode().splitlines()[0]

    def test_products_csv_leaves_out_cost_and_stock_by_default(self):
        grant(self.user, "export_products")
        header = self._csv_header()
        self.assertNotIn("Cost Price", header)
        self.assertNotIn("Stock", header)

    def test_products_csv_includes_them_with_permission(self):
        grant(self.user, "export_products", "view_cost_price", "view_stock")
        header = self._csv_header()
        self.assertIn("Cost Price", header)
        self.assertIn("Stock", header)

    def test_dashboard_hides_stock_value_without_valuation_permission(self):
        grant(self.user, "view_sales_reports")
        self.assertNotContains(self.client.get(reverse("dashboard")), "Stock value")
        grant(self.user, "view_stock_valuation")
        self.assertContains(self.client.get(reverse("dashboard")), "Stock value")


class StaffManagementRuleTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("fp-boss", "", "pw-Boss-12345")
        # The manager preset holds every permission except assign_permissions.
        self.manager = staff_from_preset(self.owner, "fp-manager", "manager")
        self.cashier = staff_from_preset(self.owner, "fp-cashier", "cashier")

    def _edit_payload(self, target, preset, codenames):
        ids = [
            str(p.pk)
            for p in Permission.objects.filter(content_type__app_label="inventory", codename__in=codenames)
        ]
        return {
            "username": target.username, "is_active": "on", "preset": preset, "permissions": ids,
        }

    def test_manager_without_assign_permissions_cannot_change_someones_access(self):
        self.client.force_login(self.manager)
        before = sorted(self.cashier.get_all_permissions())
        self.client.post(
            reverse("settings_staff_update", args=[self.cashier.pk]),
            self._edit_payload(self.cashier, "owner", PRESETS["owner"]["permissions"]),
        )
        self.cashier = User.objects.get(pk=self.cashier.pk)
        self.assertEqual(sorted(self.cashier.get_all_permissions()), before)
        self.assertFalse(has_access(self.cashier, "manage_store_settings"))

    def test_account_created_without_assign_permissions_starts_with_no_access(self):
        self.client.force_login(self.manager)
        payload = self._edit_payload(self.cashier, "manager", PRESETS["manager"]["permissions"])
        payload.update({"username": "fp-newhire", "password": "pw-NewHire-12345"})
        self.client.post(reverse("settings_staff_create"), payload)

        newcomer = User.objects.get(username="fp-newhire")
        self.assertFalse(has_access(newcomer, "access_pos"))
        self.assertFalse(has_access(newcomer, "view_products"))
        self.assertFalse(newcomer.is_staff)

    def test_owner_can_still_assign_access(self):
        self.client.force_login(self.owner)
        self.client.post(
            reverse("settings_staff_update", args=[self.cashier.pk]),
            self._edit_payload(self.cashier, "custom", ["access_pos", "create_sale", "view_products"]),
        )
        self.cashier = User.objects.get(pk=self.cashier.pk)
        self.assertTrue(has_access(self.cashier, "view_products"))

    def test_owner_cannot_deactivate_their_own_account(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("settings_staff_update", args=[self.owner.pk]),
            {"username": self.owner.username, "preset": "owner"},
        )
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)
        self.assertContains(response, "cannot deactivate your own account")

    def test_manager_cannot_edit_their_own_access(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("settings_staff_update", args=[self.manager.pk]))
        self.assertEqual(response.status_code, 302)


class AccessAuditCommandTests(TestCase):
    def test_flags_admin_site_exposure_and_unconfigured_accounts(self):
        User.objects.create_superuser("audit-boss", "", "pw-Boss-12345")
        exposed = User.objects.create_user("audit-exposed", password="pw-Audit-12345", is_staff=True)
        UserProfile.objects.create(user=exposed, role=UserProfile.ROLE_ADMIN)

        output = io.StringIO()
        call_command("audit_access", stdout=output)
        report = output.getvalue()
        self.assertIn("audit-exposed", report)
        self.assertIn("Django admin", report)
        self.assertIn("LEGACY role fallback", report)

    def test_reports_clean_when_nothing_is_wrong(self):
        User.objects.create_superuser("audit-only-boss", "", "pw-Boss-12345")
        output = io.StringIO()
        call_command("audit_access", stdout=output)
        self.assertIn("No access problems found", output.getvalue())
