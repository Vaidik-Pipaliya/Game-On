from datetime import datetime, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

from .models import Booking, SocialSession
from .services import book_court, create_social_session
from .tests import IST, Fixtures


def future_day(weekday=None, min_days=3):
    """A club-local date a few days ahead, optionally forced to a weekday (Mon=0 ... Fri=4)."""
    day = timezone.localdate() + timedelta(days=min_days)
    while weekday is not None and day.weekday() != weekday:
        day += timedelta(days=1)
    return day


def local(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


class ScreenTestCase(Fixtures, TestCase):
    def setUp(self):
        self.make_world()
        self.owner = User.objects.create(username="o", email="o@example.com", role="owner")
        self.desk = User.objects.create(username="d", email="d@example.com", role="front_desk")
        self.plain = User.objects.create(username="m", email="m@example.com", role="member")
        self.day = future_day()
        self.client.force_login(self.desk)

    def booking_data(self, **overrides):
        data = {"court": self.court1.pk, "start": f"{self.day.isoformat()}T18:00", "member_phone": "",
                "guest_name": "Asha", "guest_phone": "98765 00001"}
        return {**data, **overrides}


class PermissionTests(ScreenTestCase):
    def test_members_and_anonymous_users_cannot_use_staff_screens(self):
        urls = [reverse(n) for n in ("booking_grid", "booking_new", "social_list")]
        self.client.force_login(self.plain)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.client.post(reverse("booking_new"), self.booking_data())
        self.assertEqual(Booking.objects.count(), 0)
        self.client.logout()
        self.assertEqual(self.client.get(urls[0]).status_code, 302)

    def test_only_owner_can_create_social_sessions(self):
        friday = future_day(weekday=4)
        data = {"court": self.court1.pk, "start": f"{friday.isoformat()}T19:00", "capacity": 12, "price_rupees": 200}
        self.assertEqual(self.client.post(reverse("social_create"), data).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse("social_create"), data).status_code, 302)
        self.assertEqual(SocialSession.objects.count(), 1)


class GridAndBookingScreenTests(ScreenTestCase):
    def test_grid_shows_courts_and_booking_links(self):
        response = self.client.get(reverse("booking_grid"), {"date": self.day.isoformat()})
        self.assertContains(response, "Court 1")
        self.assertContains(response, f"court={self.court1.pk}&amp;start={self.day.isoformat()}T18:00")

    def test_grid_with_garbage_date_falls_back_to_today(self):
        self.assertEqual(self.client.get(reverse("booking_grid"), {"date": "nonsense"}).status_code, 200)

    def test_booking_form_is_prefilled_from_the_grid_link(self):
        response = self.client.get(reverse("booking_new"), {"court": self.court1.pk, "start": f"{self.day.isoformat()}T18:00"})
        self.assertContains(response, "Court 1")
        self.assertContains(response, "18:00")

    def test_walk_in_booking_is_created_at_walk_in_rate(self):
        response = self.client.post(reverse("booking_new"), self.booking_data())
        self.assertEqual(response.status_code, 302)
        booking = Booking.objects.get()
        self.assertEqual((booking.guest_name, booking.guest_phone, booking.price_paise), ("Asha", "9876500001", 80000))
        self.assertEqual(booking.created_by, self.desk)

    def test_member_booking_by_phone_gets_member_price(self):
        member = self.make_member(self.silver)
        self.client.post(reverse("booking_new"), self.booking_data(member_phone=member.phone, guest_name="", guest_phone=""))
        self.assertEqual(Booking.objects.get().price_paise, 68000)

    def test_unknown_member_phone_shows_error_and_books_nothing(self):
        response = self.client.post(reverse("booking_new"), self.booking_data(member_phone="9111111111"))
        self.assertContains(response, "No member has this phone number")
        self.assertEqual(Booking.objects.count(), 0)

    def test_guest_without_name_is_rejected(self):
        response = self.client.post(reverse("booking_new"), self.booking_data(guest_name=""))
        self.assertContains(response, "Enter the guest")
        self.assertEqual(Booking.objects.count(), 0)

    def test_taken_slot_shows_friendly_message(self):
        book_court(court=self.court1, start=local(self.day, 18), guest_name="A", guest_phone="1")
        response = self.client.post(reverse("booking_new"), self.booking_data(start=f"{self.day.isoformat()}T18:30"))
        self.assertContains(response, "Court 1 is taken at 18:30. Pick another court or time.")
        self.assertEqual(Booking.objects.count(), 1)

    def test_third_booking_for_member_shows_limit_message(self):
        member = self.make_member(self.silver)
        for hour in (8, 10):
            book_court(court=self.court1, start=local(self.day, hour), member=member)
        response = self.client.post(reverse("booking_new"), self.booking_data(
            member_phone=member.phone, guest_name="", guest_phone="", start=f"{self.day.isoformat()}T12:00"))
        self.assertContains(response, "reached the limit of 2 bookings")

    def test_cancel_marks_booking_cancelled_and_needs_post(self):
        booking = book_court(court=self.court1, start=local(self.day, 18), guest_name="A", guest_phone="1")
        url = reverse("booking_cancel", args=[booking.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, follow=True)
        self.assertContains(response, "Booking cancelled")
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)


class SocialScreenTests(ScreenTestCase):
    def setUp(self):
        super().setUp()
        self.friday = future_day(weekday=4)
        self.session = create_social_session(
            court=self.court1, start=local(self.friday, 19), capacity=2, price_per_player_paise=20000, created_by=self.owner
        )

    def join(self, **data):
        return self.client.post(reverse("social_join", args=[self.session.pk]), data, follow=True)

    def test_page_lists_session_with_places_taken(self):
        self.assertContains(self.client.get(reverse("social_list")), "0 / 2")

    def test_front_desk_adds_players_until_full(self):
        self.assertContains(self.join(guest_name="A", guest_phone="9876500001"), "Player added")
        self.join(guest_name="B", guest_phone="9876500002")
        self.assertContains(self.join(guest_name="C", guest_phone="9876500003"), "is full")

    def test_non_friday_is_rejected_with_message(self):
        self.client.force_login(self.owner)
        saturday = self.friday + timedelta(days=1)
        response = self.client.post(reverse("social_create"), {
            "court": self.court2.pk, "start": f"{saturday.isoformat()}T19:00", "capacity": 6, "price_rupees": 100,
        }, follow=True)
        self.assertContains(response, "runs on Fridays")
