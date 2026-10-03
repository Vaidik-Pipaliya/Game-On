from datetime import date, timedelta

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Member, Membership, Plan
from .services import register_member, renew_membership, search_members, send_renewal_reminders

TODAY = date(2026, 10, 3)


def make_plans():
    gold = Plan.objects.create(name="Gold", price_paise=1200000, duration_days=365)
    junior = Plan.objects.create(name="Junior", price_paise=300000, duration_days=365, junior_only=True)
    return gold, junior


def make_member(name="Aarav Sharma", phone="9876500001", plan=None, ends=None, email="a@example.com", born=1990):
    member = Member.objects.create(full_name=name, phone=phone, email=email, date_of_birth=date(born, 6, 15))
    if plan:
        Membership.objects.create(member=member, plan=plan, start_date=TODAY - timedelta(days=365), end_date=ends)
    return member


class MembershipStatusTests(TestCase):
    def setUp(self):
        self.gold, _ = make_plans()
        self.member = make_member()

    def status(self, days_left, cancelled=False):
        m = Membership.objects.create(
            member=self.member, plan=self.gold, start_date=TODAY,
            end_date=TODAY + timedelta(days=days_left), cancelled=cancelled,
        )
        return m.status_on(TODAY)

    def test_active_when_more_than_14_days_left(self):
        self.assertEqual(self.status(15), "active")

    def test_expiring_at_14_days_and_on_last_day(self):
        self.assertEqual(self.status(14), "expiring")
        self.assertEqual(self.status(0), "expiring")

    def test_expired_the_day_after_end_date(self):
        self.assertEqual(self.status(-1), "expired")

    def test_cancelled_wins(self):
        self.assertEqual(self.status(100, cancelled=True), "cancelled")


class RegisterMemberTests(TestCase):
    def setUp(self):
        self.gold, self.junior = make_plans()
        self.base = dict(full_name="New Person", phone="9000000010", email="n@example.com", today=TODAY)

    def test_adult_gets_membership_for_plan_duration(self):
        member = register_member(**self.base, date_of_birth=date(1990, 1, 1), plan=self.gold)
        membership = member.current_membership
        self.assertEqual(membership.start_date, TODAY)
        self.assertEqual(membership.end_date, TODAY + timedelta(days=365))

    def test_junior_with_adult_guardian_is_accepted(self):
        guardian = make_member()
        member = register_member(**self.base, date_of_birth=date(2013, 5, 1), plan=self.junior, guardian=guardian)
        self.assertEqual(member.guardian, guardian)

    def test_junior_without_guardian_is_rejected_and_nothing_saved(self):
        with self.assertRaises(ValidationError):
            register_member(**self.base, date_of_birth=date(2013, 5, 1), plan=self.junior)
        self.assertFalse(Member.objects.filter(phone="9000000010").exists())

    def test_adult_cannot_take_junior_plan(self):
        with self.assertRaises(ValidationError):
            register_member(**self.base, date_of_birth=date(1990, 1, 1), plan=self.junior, guardian=make_member())

    def test_minor_cannot_take_adult_plan(self):
        with self.assertRaises(ValidationError):
            register_member(**self.base, date_of_birth=date(2013, 5, 1), plan=self.gold)

    def test_guardian_must_be_adult(self):
        child_guardian = make_member(born=2012)
        with self.assertRaises(ValidationError):
            register_member(**self.base, date_of_birth=date(2013, 5, 1), plan=self.junior, guardian=child_guardian)

    def test_turning_18_today_counts_as_adult(self):
        member = register_member(**self.base, date_of_birth=date(2008, 10, 3), plan=self.gold)
        self.assertIsNotNone(member.pk)


class RenewTests(TestCase):
    def setUp(self):
        self.gold, _ = make_plans()

    def test_unexpired_membership_is_extended_from_its_end_date(self):
        member = make_member(plan=self.gold, ends=TODAY + timedelta(days=5))
        new = renew_membership(member, today=TODAY)
        self.assertEqual(new.start_date, TODAY + timedelta(days=6))

    def test_expired_membership_restarts_today(self):
        member = make_member(plan=self.gold, ends=TODAY - timedelta(days=10))
        new = renew_membership(member, today=TODAY)
        self.assertEqual(new.start_date, TODAY)

    def test_member_without_membership_cannot_renew(self):
        with self.assertRaises(ValidationError):
            renew_membership(make_member(), today=TODAY)


class SearchTests(TestCase):
    def setUp(self):
        make_member("Priya Nair", "9876500002")
        make_member("Rohan Mehta", "9876500003")

    def test_search_by_part_of_name_ignores_case(self):
        self.assertEqual([m.full_name for m in search_members("priya")], ["Priya Nair"])

    def test_search_by_part_of_phone(self):
        self.assertEqual([m.full_name for m in search_members("0003")], ["Rohan Mehta"])

    def test_empty_search_lists_members(self):
        self.assertEqual(len(search_members("")), 2)


class ReminderTests(TestCase):
    def setUp(self):
        self.gold, _ = make_plans()

    def test_reminder_sent_at_7_days_but_not_twice_on_same_day(self):
        make_member(plan=self.gold, ends=TODAY + timedelta(days=7))
        self.assertEqual(send_renewal_reminders(today=TODAY), 1)
        self.assertEqual(send_renewal_reminders(today=TODAY), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("7 days", mail.outbox[0].subject)

    def test_no_reminder_on_other_days(self):
        make_member(plan=self.gold, ends=TODAY + timedelta(days=10))
        self.assertEqual(send_renewal_reminders(today=TODAY), 0)

    def test_no_reminder_if_already_renewed(self):
        member = make_member(plan=self.gold, ends=TODAY + timedelta(days=7))
        Membership.objects.create(
            member=member, plan=self.gold, start_date=TODAY + timedelta(days=8), end_date=TODAY + timedelta(days=373)
        )
        self.assertEqual(send_renewal_reminders(today=TODAY), 0)

    def test_member_without_email_is_skipped(self):
        make_member(plan=self.gold, ends=TODAY + timedelta(days=1), email="")
        self.assertEqual(send_renewal_reminders(today=TODAY), 0)


class MemberScreenTests(TestCase):
    def setUp(self):
        self.gold, self.junior = make_plans()
        from accounts.models import User

        self.desk_user = User.objects.create(username="d", email="d@example.com", role="front_desk")
        self.member_user = User.objects.create(username="m", email="m@example.com", role="member")

    def form_data(self, **overrides):
        data = {"full_name": "Kavya Rao", "phone": "+91 98765 43210", "email": "k@example.com",
                "date_of_birth": "1992-04-05", "plan": self.gold.pk}
        return {**data, **overrides}

    def test_member_role_is_forbidden(self):
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get("/desk/members/").status_code, 403)
        self.assertEqual(self.client.post("/desk/members/new/", self.form_data()).status_code, 403)

    def test_front_desk_registers_member_and_phone_is_normalised(self):
        self.client.force_login(self.desk_user)
        response = self.client.post("/desk/members/new/", self.form_data())
        member = Member.objects.get(full_name="Kavya Rao")
        self.assertRedirects(response, f"/desk/members/{member.pk}/")
        self.assertEqual(member.phone, "9876543210")

    def test_duplicate_phone_is_rejected_with_message(self):
        make_member(phone="9876543210")
        self.client.force_login(self.desk_user)
        response = self.client.post("/desk/members/new/", self.form_data())
        self.assertContains(response, "already exists")
        self.assertEqual(Member.objects.count(), 1)

    def test_junior_without_guardian_shows_friendly_error(self):
        self.client.force_login(self.desk_user)
        response = self.client.post("/desk/members/new/", self.form_data(date_of_birth="2014-01-01", plan=self.junior.pk))
        self.assertContains(response, "needs a guardian")

    def test_profile_and_search_pages_render(self):
        member = make_member("Priya Nair", plan=self.gold, ends=date.today() + timedelta(days=5))
        self.client.force_login(self.desk_user)
        self.assertContains(self.client.get(f"/desk/members/{member.pk}/"), "Expiring")
        self.assertContains(self.client.get("/desk/members/?q=priya"), "Priya Nair")
