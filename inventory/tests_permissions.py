from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from .access_control import ALL_CODENAMES
from .models import UserProfile
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
        manager = User.objects.create_user("manager", password="pw", is_staff=True)
        UserProfile.objects.create(user=manager, role=UserProfile.ROLE_ADMIN)
        manager.user_permissions.add(Permission.objects.get(content_type__app_label="inventory", codename="edit_staff"))
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
