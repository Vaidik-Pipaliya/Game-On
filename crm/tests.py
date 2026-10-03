from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from courts.models import Booking
from courts.services import book_court
from courts.test_views import future_day, local
from courts.tests import Fixtures
from members.models import Member

from .models import Lead
from .services import create_lead, pick_assignee, update_lead


class CrmTestCase(Fixtures, TestCase):
    def setUp(self):
        cache.clear()  # the rate-limit counters live in the cache
        self.make_world()
        self.owner = User.objects.create(username="o", email="owner@example.com", role="owner")
        self.desk = User.objects.create(username="d", email="desk@example.com", role="front_desk")

    def enquiry(self, **overrides):
        data = {"name": "Rahul Verma", "phone": "98111 00001", "email": "", "sport": "", "message": "Weekend tennis?", "website": ""}
        return self.client.post(reverse("enquiry"), {**data, **overrides})


class PublicPageTests(CrmTestCase):
    def test_public_pages_need_no_login(self):
        for name in ("home", "plans", "availability", "enquiry", "enquiry_thanks"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_home_has_local_business_structured_data(self):
        self.assertContains(self.client.get(reverse("home")), '"@type": "SportsActivityLocation"')

    def test_availability_shows_free_and_taken_but_never_names(self):
        day = future_day()
        book_court(court=self.court1, start=local(day, 18), guest_name="Secret Person", guest_phone="9000000001")
        response = self.client.get(reverse("availability"))
        self.assertContains(response, "Court 1")
        self.assertNotContains(response, "Secret Person")


class EnquiryTests(CrmTestCase):
    def test_enquiry_creates_assigned_lead_and_emails_the_assignee(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.enquiry()
        self.assertRedirects(response, reverse("enquiry_thanks"))
        lead = Lead.objects.get()
        self.assertEqual((lead.status, lead.source, lead.phone), ("new", "website", "9811100001"))
        self.assertIsNotNone(lead.assigned_to)
        self.assertEqual(lead.follow_up_date, timezone.localdate() + timedelta(days=1))
        self.assertEqual(mail.outbox[0].to, [lead.assigned_to.email])

    def test_bots_filling_the_hidden_field_are_ignored_silently(self):
        response = self.enquiry(website="http://spam.example")
        self.assertRedirects(response, reverse("enquiry_thanks"))
        self.assertEqual(Lead.objects.count(), 0)

    def test_rate_limit_after_five_submissions_per_hour(self):
        for _ in range(5):
            self.enquiry()
        response = self.enquiry()
        self.assertContains(response, "Too many requests")
        self.assertEqual(Lead.objects.count(), 5)

    def test_invalid_phone_shows_error(self):
        self.assertContains(self.enquiry(phone="123"), "Enter a 10-digit mobile number")

    def test_leads_are_shared_evenly(self):
        first = create_lead(name="A", phone="9000000001")
        second = create_lead(name="B", phone="9000000002")
        self.assertNotEqual(first.assigned_to, second.assigned_to)
        update_lead(first, status="won")
        self.assertEqual(pick_assignee(), first.assigned_to)  # closed leads don't count as workload


class TrialTests(CrmTestCase):
    def setUp(self):
        super().setUp()
        self.visitor = User.objects.create(username="v", email="visitor@example.com", role="member", first_name="Sneha")
        self.client.force_login(self.visitor)

    def trial(self, start, court=None):
        return self.client.post(reverse("trial"), {
            "name": "Sneha Kapoor", "phone": "9811100002", "court": (court or self.court1).pk,
            "start": start.strftime("%Y-%m-%dT%H:%M"), "website": "",
        })

    def test_anonymous_visitors_must_sign_in_first_and_book_nothing(self):
        self.client.logout()
        start = local(future_day(), 17)
        response = self.trial(start)
        self.assertRedirects(response, f"/login/?next={reverse('trial')}", fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("trial")).status_code, 302)
        self.assertEqual((Booking.objects.count(), Lead.objects.count()), (0, 0))

    def test_signed_in_visitor_sees_the_form_prefilled_with_their_name(self):
        response = self.client.get(reverse("trial"))
        self.assertContains(response, "visitor@example.com")
        self.assertContains(response, 'value="Sneha"')

    def test_trial_books_the_court_and_creates_a_linked_lead_with_the_verified_email(self):
        start = local(future_day(), 17)
        self.assertRedirects(self.trial(start), reverse("enquiry_thanks"))
        lead = Lead.objects.get()
        self.assertEqual((lead.source, lead.email, lead.trial_booking.start, lead.trial_booking.price_paise),
                         ("trial", "visitor@example.com", start, 80000))
        self.assertEqual(lead.trial_booking.created_by, self.visitor)
        self.assertFalse(lead.trial_booking.is_paid)  # pay at the club

    def test_only_one_upcoming_trial_per_account(self):
        day = future_day()
        self.trial(local(day, 17))
        response = self.trial(local(day, 19), court=self.court2)
        self.assertContains(response, "You already have a trial booked")
        self.assertEqual(Booking.objects.count(), 1)

    def test_a_cancelled_trial_frees_the_account_to_try_again(self):
        day = future_day()
        self.trial(local(day, 17))
        Booking.objects.update(status="cancelled")
        self.trial(local(day, 19))
        self.assertEqual(Booking.objects.filter(status="confirmed").count(), 1)

    def test_existing_members_are_sent_to_the_member_booking_page(self):
        member = self.make_member(self.silver)
        member.user = self.visitor
        member.save()
        self.assertRedirects(self.client.get(reverse("trial")), reverse("portal_grid"), fetch_redirect_response=False)

    def test_taken_slot_shows_message_and_saves_no_lead(self):
        start = local(future_day(), 17)
        book_court(court=self.court1, start=start, guest_name="X", guest_phone="9000000009")
        self.assertContains(self.trial(start), "is taken at 17:00")
        self.assertEqual((Lead.objects.count(), Booking.objects.count()), (0, 1))


class LeadPipelineTests(CrmTestCase):
    def test_lost_needs_a_reason_and_closed_leads_have_no_follow_up(self):
        lead = create_lead(name="A", phone="9000000001")
        with self.assertRaises(ValidationError):
            update_lead(lead, status="lost")
        update_lead(lead, status="lost", lost_reason="Too far away", follow_up_date=timezone.localdate())
        lead.refresh_from_db()
        self.assertEqual((lead.status, lead.follow_up_date, lead.lost_reason), ("lost", None, "Too far away"))

    def test_leads_screen_permissions_and_overdue_flag(self):
        lead = create_lead(name="Late Lead", phone="9000000001")
        Lead.objects.filter(pk=lead.pk).update(follow_up_date=timezone.localdate() - timedelta(days=2))
        self.client.force_login(User.objects.create(username="b", email="b@example.com", role="bar_staff"))
        self.assertEqual(self.client.get(reverse("lead_list")).status_code, 403)
        self.client.force_login(self.desk)
        response = self.client.get(reverse("lead_list"))
        self.assertContains(response, "Late Lead")
        self.assertContains(response, "Overdue")

    def test_staff_logs_phone_enquiry(self):
        self.client.force_login(self.desk)
        self.client.post(reverse("lead_list"), {"name": "Caller", "phone": "9000000003", "source": "phone", "email": "", "sport_interest": "Padel", "message": ""})
        self.assertEqual(Lead.objects.get().source, "phone")

    def test_register_as_member_converts_the_lead(self):
        lead = create_lead(name="Pooja Shetty", phone="9811100004", email="pooja@example.com")
        self.client.force_login(self.desk)
        url = reverse("member_new") + f"?lead={lead.pk}&full_name=Pooja+Shetty&phone=9811100004&email=pooja%40example.com"
        self.assertContains(self.client.get(url), 'value="Pooja Shetty"')
        self.client.post(url, {"full_name": "Pooja Shetty", "phone": "9811100004", "email": "pooja@example.com",
                               "date_of_birth": "1994-02-02", "plan": self.silver.pk, "payment_method": "upi"})
        lead.refresh_from_db()
        self.assertEqual((lead.status, lead.converted_member), ("won", Member.objects.get(phone="9811100004")))
