from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from .models import UserProfile
from .permission_forms import StaffAccessForm
from .stocktake_models import StocktakeSession, StocktakeZone


class GranularStocktakeEntryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner", "owner@example.com", "pw")
        self.counter = User.objects.create_user("counter_only", password="pw", is_staff=True)
        UserProfile.objects.create(user=self.counter, role=UserProfile.ROLE_ADMIN)
        self.count_permission = Permission.objects.get(
            content_type__app_label="inventory",
            codename="count_assigned_zones",
        )
        self.view_permission = Permission.objects.get(
            content_type__app_label="inventory",
            codename="view_assigned_stocktakes",
        )
        self.complete_permission = Permission.objects.get(
            content_type__app_label="inventory",
            codename="complete_stocktake_zones",
        )
        self.counter.user_permissions.add(self.count_permission, self.view_permission, self.complete_permission)
        self.session = StocktakeSession.objects.create(
            name="Opening Count",
            created_by=self.owner,
            status=StocktakeSession.STATUS_COUNTING,
        )
        self.zone = StocktakeZone.objects.create(session=self.session, name="Main shelf")
        self.zone.assigned_users.add(self.counter)
        self.client.force_login(self.counter)

    def test_counter_permission_can_open_stocktake_list_detail_and_count_page(self):
        self.assertEqual(self.client.get(reverse("stocktake_list")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("stocktake_detail", args=[self.session.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("stocktake_count_zone", args=[self.zone.pk])).status_code,
            200,
        )

    def test_counter_cannot_open_unassigned_zone(self):
        other_zone = StocktakeZone.objects.create(session=self.session, name="Store room")
        response = self.client.get(reverse("stocktake_count_zone", args=[other_zone.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("stocktake_list"))

    def test_counter_still_cannot_create_stocktake_session(self):
        response = self.client.get(reverse("stocktake_create"))
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.url, reverse("stocktake_create"))

    def test_saved_permissions_remain_selected_when_editor_reopens(self):
        form = StaffAccessForm(
            data={
                "username": self.counter.username,
                "first_name": "Test",
                "last_name": "Counter",
                "email": "",
                "is_active": "on",
                "preset": "inventory_counter",
                "permissions": [
                    self.view_permission.pk,
                    self.count_permission.pk,
                    self.complete_permission.pk,
                ],
            },
            instance=self.counter,
            actor=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        reopened = StaffAccessForm(instance=User.objects.get(pk=self.counter.pk), actor=self.owner)
        checked_codes = {
            entry["codename"]
            for _, entries in reopened.permission_sections
            for entry in entries
            if entry["checked"]
        }
        self.assertEqual(
            checked_codes,
            {"view_assigned_stocktakes", "count_assigned_zones", "complete_stocktake_zones"},
        )
        self.assertEqual(reopened.fields["preset"].initial, "custom")
