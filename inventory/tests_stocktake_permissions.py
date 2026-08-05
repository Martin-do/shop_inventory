from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from .models import UserProfile
from .stocktake_models import StocktakeSession, StocktakeZone


class GranularStocktakeEntryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner", "owner@example.com", "pw")
        self.counter = User.objects.create_user("counter_only", password="pw", is_staff=True)
        UserProfile.objects.create(user=self.counter, role=UserProfile.ROLE_STOCK_CLERK)
        permission = Permission.objects.get(
            content_type__app_label="inventory",
            codename="count_assigned_zones",
        )
        self.counter.user_permissions.add(permission)
        self.session = StocktakeSession.objects.create(
            name="Opening Count",
            created_by=self.owner,
            status=StocktakeSession.STATUS_COUNTING,
        )
        self.zone = StocktakeZone.objects.create(session=self.session, name="Main shelf")
        self.zone.assigned_users.add(self.counter)
        self.client.force_login(self.counter)

    def test_counter_permission_can_open_stocktake_list_and_assigned_detail(self):
        self.assertEqual(self.client.get(reverse("stocktake_list")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("stocktake_detail", args=[self.session.pk])).status_code,
            200,
        )

    def test_counter_still_cannot_create_stocktake_session(self):
        response = self.client.get(reverse("stocktake_create"))
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.url, reverse("stocktake_create"))
