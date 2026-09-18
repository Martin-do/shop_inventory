import json
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from .access_control import ALL_CODENAMES, PRESETS
from .models import Product, Sale, StockMovement, UserProfile
from .permission_forms import StaffAccessForm


class GranularPermissionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner", "owner@example.com", "pw")
        self.cashier = User.objects.create_user("cashier", password="pw", is_staff=True)
        UserProfile.objects.create(user=self.cashier, role=UserProfile.ROLE_ADMIN)
        self.access_pos = Permission.objects.get(content_type__app_label="inventory", codename="access_pos")
        self.create_sale = Permission.objects.get(content_type__app_label="inventory", codename="create_sale")
        self.view_reports = Permission.objects.get(content_type__app_label="inventory", codename="view_sales_reports")

    def test_permission_catalogue_created(self):
        count = Permission.objects.filter(content_type__app_label="inventory", codename__in=ALL_CODENAMES).count()
        self.assertEqual(count, len(ALL_CODENAMES))

    def test_user_can_open_only_permitted_module(self):
        self.cashier.user_permissions.add(self.access_pos, self.create_sale)
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse("pos")).status_code, 200)
        response = self.client.get(reverse("reports"))
        self.assertRedirects(response, reverse("pos"), fetch_redirect_response=False)

    def test_navigation_hides_unpermitted_modules(self):
        self.cashier.user_permissions.add(self.access_pos)
        self.client.force_login(self.cashier)
        response = self.client.get(reverse("pos"))
        self.assertContains(response, ">POS<", html=False)
        self.assertNotContains(response, ">Reports<", html=False)
        self.assertNotContains(response, ">History<", html=False)

    def test_superuser_can_open_permission_editor(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("settings_staff_update", args=[self.cashier.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Individual permissions")
        self.assertContains(response, "Apply opening inventory")

    def test_permission_editor_explains_each_capability(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("settings_staff_update", args=[self.cashier.pk]))
        self.assertContains(response, 'class="permission-info"', html=False)
        self.assertContains(response, "Allows cancelling a completed sale and restoring its sold quantities to stock.", html=False)
        self.assertContains(response, "Tap or hover briefly", html=False)

    def test_non_superuser_cannot_grant_permission_they_lack(self):
        # The manager may assign permissions, but only ones they hold themselves.
        manager = User.objects.create_user("manager", password="pw", is_staff=True)
        UserProfile.objects.create(user=manager, role=UserProfile.ROLE_ADMIN)
        manager.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="inventory", codename__in=["edit_staff", "assign_permissions"]
        ))
        form = StaffAccessForm(
            data={
                "username": self.cashier.username,
                "first_name": "",
                "last_name": "",
                "email": "",
                "is_active": "on",
                "preset": "custom",
                "permissions": [self.view_reports.pk],
            },
            instance=self.cashier,
            actor=manager,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("cannot grant permissions", str(form.errors).lower())

    def test_editing_staff_without_assign_permissions_never_changes_access(self):
        manager = User.objects.create_user("detail-editor", password="pw", is_staff=True)
        UserProfile.objects.create(user=manager, role=UserProfile.ROLE_ADMIN)
        manager.user_permissions.add(Permission.objects.get(content_type__app_label="inventory", codename="edit_staff"))
        form = StaffAccessForm(
            data={
                "username": self.cashier.username,
                "first_name": "Renamed",
                "last_name": "",
                "email": "",
                "is_active": "on",
                "preset": "custom",
                "permissions": [self.view_reports.pk],
            },
            instance=self.cashier,
            actor=manager,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.cashier.refresh_from_db()
        self.assertEqual(self.cashier.first_name, "Renamed")
        self.assertFalse(self.cashier.user_permissions.filter(codename="view_sales_reports").exists())

    def test_exact_custom_selection_is_saved(self):
        form = StaffAccessForm(
            data={
                "username": self.cashier.username,
                "first_name": "",
                "last_name": "",
                "email": "",
                "is_active": "on",
                "preset": "cashier",
                "permissions": [self.access_pos.pk],
            },
            instance=self.cashier,
            actor=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        user = User.objects.get(pk=user.pk)
        self.assertTrue(user.has_perm("inventory.access_pos"))
        self.assertFalse(user.has_perm("inventory.create_sale"))
        self.assertFalse(user.groups.filter(name="Cashier").exists())

    def test_legacy_roles_work_when_created_after_migrations(self):
        legacy_cashier = User.objects.create_user("legacy_cashier", password="pw")
        UserProfile.objects.create(user=legacy_cashier, role=UserProfile.ROLE_CASHIER)
        self.client.force_login(legacy_cashier)
        self.assertEqual(self.client.get(reverse("pos")).status_code, 200)

        legacy_clerk = User.objects.create_user("legacy_clerk", password="pw", is_staff=True)
        UserProfile.objects.create(user=legacy_clerk, role=UserProfile.ROLE_STOCK_CLERK)
        self.client.force_login(legacy_clerk)
        self.assertEqual(self.client.get(reverse("receive_stock")).status_code, 200)

        legacy_admin = User.objects.create_user("legacy_admin", password="pw", is_staff=True)
        UserProfile.objects.create(user=legacy_admin, role=UserProfile.ROLE_ADMIN)
        self.client.force_login(legacy_admin)
        self.assertEqual(self.client.get(reverse("audit_history")).status_code, 200)

    def test_explicit_zero_access_does_not_fall_back_to_legacy_admin(self):
        form = StaffAccessForm(
            data={
                "username": self.cashier.username,
                "first_name": "",
                "last_name": "",
                "email": "",
                "is_active": "on",
                "preset": "custom",
                "permissions": [],
            },
            instance=self.cashier,
            actor=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertTrue(user.groups.filter(name="Custom").exists())
        self.client.force_login(user)
        response = self.client.get(reverse("pos"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)


class EnforcedPermissionTests(TestCase):
    """Regression cover for access holes found during the pre-deployment audit."""

    def setUp(self):
        self.owner = User.objects.create_superuser("audit-owner", "", "pw-Owner-12345")
        self.product = Product.objects.create(name="Milk", barcode="9111", selling_price=Decimal("1000.00"))
        StockMovement.objects.create(product=self.product, movement_type=StockMovement.RECEIVE, quantity=10)
        self.owners_sale = Sale.objects.create(
            cashier=self.owner, cashier_name="audit-owner",
            total=Decimal("1000.00"), amount_paid=Decimal("1000.00"),
        )

    def _staff(self, username, preset):
        permission_ids = [
            str(permission.pk)
            for permission in Permission.objects.filter(
                content_type__app_label="inventory", codename__in=PRESETS[preset]["permissions"]
            )
        ]
        form = StaffAccessForm(
            {
                "username": username, "password": "pw-Staff-12345", "is_active": "on",
                "preset": preset, "permissions": permission_ids,
            },
            actor=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.client.force_login(user)
        return user

    def test_cashier_cannot_reverse_a_sale(self):
        self._staff("till-1", "cashier")
        response = self.client.post(
            reverse("sale_revert", args=[self.owners_sale.pk]),
            {"reversal_reason": "Attempted reversal without permission"},
        )
        self.owners_sale.refresh_from_db()
        self.assertEqual(self.owners_sale.status, Sale.STATUS_COMPLETED)
        self.assertEqual(response.status_code, 302)

    def test_senior_cashier_can_reverse_a_sale(self):
        self._staff("till-lead", "senior_cashier")
        self.client.post(
            reverse("sale_revert", args=[self.owners_sale.pk]),
            {"reversal_reason": "Customer returned the goods"},
        )
        self.owners_sale.refresh_from_db()
        self.assertEqual(self.owners_sale.status, Sale.STATUS_REVERTED)

    def test_cashier_cannot_open_another_cashiers_receipt(self):
        self._staff("till-2", "cashier")
        response = self.client.get(reverse("sale_receipt", args=[self.owners_sale.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(str(self.owners_sale.total), response.content.decode())

    def test_cashier_can_open_their_own_receipt(self):
        user = self._staff("till-3", "cashier")
        own_sale = Sale.objects.create(
            cashier=user, cashier_name=user.username,
            total=Decimal("500.00"), amount_paid=Decimal("500.00"),
        )
        self.assertEqual(self.client.get(reverse("sale_receipt", args=[own_sale.pk])).status_code, 200)

    def test_counter_cannot_open_sales_at_all(self):
        self._staff("counter-1", "inventory_counter")
        self.assertEqual(self.client.get(reverse("sale_detail", args=[self.owners_sale.pk])).status_code, 302)

    def test_cashier_preset_can_submit_a_sale(self):
        """Checkout goes through the sync endpoint, so cashiers must be allowed to use it."""
        self._staff("till-4", "cashier")
        response = self.client.post(
            reverse("api_sync_offline"),
            json.dumps({"sales": [{"temp_receipt": "T-1", "items": [{"barcode": "9111", "quantity": 1}], "amount_paid": 1000}]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["synced"]), 1)

    def test_cashier_without_discount_permission_is_refused(self):
        self._staff("till-5", "cashier")
        response = self.client.post(
            reverse("api_sync_offline"),
            json.dumps({"sales": [{"temp_receipt": "T-2", "items": [{"barcode": "9111", "quantity": 1}], "discount_amount": 900, "amount_paid": 100}]}),
            content_type="application/json",
        )
        self.assertEqual(Sale.objects.filter(client_reference="T-2").count(), 0)
        self.assertIn("discount", response.json()["errors"][0]["error"].lower())

    def test_staff_accounts_cannot_reach_the_django_admin(self):
        self._staff("till-6", "cashier")
        response = self.client.get("/admin/", follow=True)
        # Without is_staff the admin site answers with its own login page.
        self.assertContains(response, "Log in", status_code=200)

    def test_staff_account_is_not_marked_as_django_staff(self):
        user = self._staff("till-7", "manager")
        self.assertFalse(user.is_staff)
