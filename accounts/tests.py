import json
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from members.models import Member

from .models import User
from .services import InvalidToken, get_or_create_user
from .templatetags.money import rupees, rupees_input

GOOGLE_CLAIMS = {"email": "Asha@Example.com", "email_verified": True, "name": "Asha Rao"}


class GetOrCreateUserTests(TestCase):
    def test_new_google_user_is_a_plain_member(self):
        user = get_or_create_user(GOOGLE_CLAIMS)
        self.assertEqual(user.role, User.Role.MEMBER)
        self.assertEqual(user.email, "asha@example.com")  # email is lower-cased
        self.assertFalse(user.has_usable_password())

    def test_second_login_reuses_user_and_keeps_staff_role(self):
        user = get_or_create_user(GOOGLE_CLAIMS)
        user.role = User.Role.FRONT_DESK
        user.save()
        again = get_or_create_user(GOOGLE_CLAIMS)
        self.assertEqual(again.pk, user.pk)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(again.role, User.Role.FRONT_DESK)

    def test_desk_registered_member_is_linked_on_first_login(self):
        member = Member.objects.create(
            full_name="Asha Rao", phone="9000000001", email="asha@example.com", date_of_birth=date(1990, 1, 1)
        )
        user = get_or_create_user(GOOGLE_CLAIMS)
        member.refresh_from_db()
        self.assertEqual(member.user, user)


class FirebaseLoginViewTests(TestCase):
    def post(self, payload):
        return self.client.post(reverse("firebase_login"), json.dumps(payload), content_type="application/json")

    @patch("accounts.views.verify_google_token", return_value=GOOGLE_CLAIMS)
    def test_valid_token_starts_session(self, _verify):
        response = self.post({"id_token": "good"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)

    @patch("accounts.views.verify_google_token", side_effect=InvalidToken("bad"))
    def test_invalid_token_is_rejected(self, _verify):
        response = self.post({"id_token": "forged"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_missing_token_is_a_400(self):
        self.assertEqual(self.post({}).status_code, 400)

    @patch("accounts.views.verify_google_token", return_value=GOOGLE_CLAIMS)
    def test_next_url_to_other_site_is_ignored(self, _verify):
        response = self.post({"id_token": "good", "next": "https://evil.example/"})
        self.assertEqual(response.json()["next"], "/")

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("firebase_login")).status_code, 405)


class RolePermissionTests(TestCase):
    def login_as(self, role):
        user = User.objects.create(username=role, email=f"{role}@example.com", role=role)
        self.client.force_login(user)

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("desk"))
        self.assertRedirects(response, "/login/?next=/desk/", fetch_redirect_response=False)

    def test_member_is_forbidden_from_staff_desk(self):
        self.login_as(User.Role.MEMBER)
        self.assertEqual(self.client.get(reverse("desk")).status_code, 403)

    def test_every_staff_role_can_open_desk(self):
        for role in ("owner", "front_desk", "bar_staff", "shop_staff"):
            with self.subTest(role=role):
                self.login_as(role)
                self.assertEqual(self.client.get(reverse("desk")).status_code, 200)


class MoneyFormatTests(TestCase):
    def test_indian_digit_grouping(self):
        self.assertEqual(
            [rupees(p) for p in (0, 5000, 120000, 12500000, 1234567850, -2640000, 99)],
            ["₹0", "₹50", "₹1,200", "₹1,25,000", "₹1,23,45,678.50", "-₹26,400", "₹0.99"],
        )
        self.assertEqual(rupees_input(135050), "1350.50")
