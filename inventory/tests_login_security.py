from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(LOGIN_ATTEMPT_LIMIT=3, LOGIN_ATTEMPT_COOLDOWN_SECONDS=300)
class LoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("till-user", password="pw-Correct-12345")
        self.login_url = reverse("login")

    def tearDown(self):
        cache.clear()

    def _attempt(self, password):
        return self.client.post(self.login_url, {"username": "till-user", "password": password})

    def test_repeated_failures_lock_out_further_attempts(self):
        for _ in range(3):
            self._attempt("wrong")

        # The correct password is now refused too: the attempt never reaches the auth view.
        self._attempt("pw-Correct-12345")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_successful_login_clears_the_counter(self):
        self._attempt("wrong")
        self._attempt("pw-Correct-12345")
        self.assertIn("_auth_user_id", self.client.session)

        self.client.logout()
        for _ in range(2):
            self._attempt("wrong")
        self._attempt("pw-Correct-12345")
        self.assertIn("_auth_user_id", self.client.session)

    def test_login_still_works_within_the_limit(self):
        self._attempt("wrong")
        self._attempt("pw-Correct-12345")
        self.assertIn("_auth_user_id", self.client.session)


class PasswordPolicyTests(TestCase):
    def test_weak_password_is_rejected_for_new_staff(self):
        from .permission_forms import StaffAccessForm

        owner = User.objects.create_superuser("policy-owner", "", "pw-Owner-12345")
        form = StaffAccessForm(
            {"username": "weak-user", "password": "1", "is_active": "on", "preset": "cashier"},
            actor=owner,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)
