from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from finance.models import Ledger, Payment
from finance.services import OnlinePaymentUnavailable

from .models import Booking
from .services import book_court
from .test_views import future_day, local
from .tests import Fixtures


class PortalCase(Fixtures, TestCase):
    def setUp(self):
        self.make_world()
        self.user = User.objects.create(username="asha", email="asha@example.com", role="member")
        self.member = self.make_member(self.silver, name="Asha Rao")
        self.member.user, self.member.email = self.user, "asha@example.com"
        self.member.save()
        self.client.force_login(self.user)
        self.day = future_day(min_days=4)

    def confirm_url(self, hour=18, court=None):
        court = court or self.court1
        return f"{reverse('portal_confirm')}?court={court.pk}&start={self.day.isoformat()}T{hour:02d}:00"

    def book_form(self, payment, hour=18):
        return self.client.post(reverse("portal_confirm"), {
            "court": self.court1.pk, "start": f"{self.day.isoformat()}T{hour:02d}:00", "payment": payment,
        })


class PortalAccessTests(PortalCase):
    def test_anonymous_goes_to_login_and_unlinked_account_gets_a_helpful_page(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("portal_grid")).status_code, 302)
        self.client.force_login(User.objects.create(username="x", email="x@example.com", role="member"))
        response = self.client.get(reverse("portal_grid"))
        self.assertContains(response, "can't find your membership", status_code=403)

    def test_grid_hides_other_peoples_names_and_limits_how_far_ahead(self):
        book_court(court=self.court1, start=local(self.day, 18), guest_name="Secret Person", guest_phone="9000000009")
        response = self.client.get(reverse("portal_grid"), {"date": self.day.isoformat()})
        self.assertContains(response, "Court 1")
        self.assertNotContains(response, "Secret Person")
        # A date far in the future is pulled back to the last bookable day (14 days ahead) instead of failing.
        far = self.client.get(reverse("portal_grid"), {"date": (timezone.localdate() + timedelta(days=90)).isoformat()})
        self.assertEqual(far.status_code, 200)
        self.assertEqual(far.context["day"], timezone.localdate() + timedelta(days=14))
        self.assertIsNone(far.context["next_day"])


class PortalBookingTests(PortalCase):
    def test_confirm_page_shows_the_members_own_price(self):
        response = self.client.get(self.confirm_url())
        self.assertContains(response, "₹680")  # ₹800 less 15% Silver discount
        self.assertContains(response, "15% member discount")

    def test_pay_at_the_club_confirms_straight_away_unpaid(self):
        response = self.book_form("club")
        self.assertRedirects(response, reverse("portal_mine"))
        booking = Booking.objects.get()
        self.assertEqual((booking.member, booking.status, booking.is_paid, booking.price_paise), (self.member, "confirmed", False, 68000))

    @patch("courts.portal.start_online_payment")
    def test_pay_online_holds_the_slot_and_opens_checkout(self, start):
        start.return_value = Payment.objects.create(razorpay_order_id="o1", source="court", reference_id=0, amount_paise=68000)
        response = self.book_form("online")
        booking = Booking.objects.get()
        self.assertEqual((booking.status, booking.hold_expires_at is not None), ("held", True))
        self.assertRedirects(response, reverse("pay_page", args=[start.return_value.pk]), fetch_redirect_response=False)
        self.assertEqual(start.call_args.kwargs["amount_paise"], 68000)

    @patch("courts.portal.start_online_payment", side_effect=OnlinePaymentUnavailable("Razorpay is not reachable right now."))
    def test_if_razorpay_is_down_the_hold_is_released_not_left_blocking_the_slot(self, _start):
        response = self.book_form("online")
        self.assertRedirects(response, reverse("portal_grid"))
        self.assertEqual(Booking.objects.get().status, "cancelled")
        book_court(court=self.court1, start=local(self.day, 18), guest_name="B", guest_phone="9000000002")  # slot is free

    def test_taken_slot_and_daily_limit_show_friendly_errors(self):
        book_court(court=self.court1, start=local(self.day, 18), guest_name="B", guest_phone="9000000002")
        self.assertContains(self.book_form("club"), "Court 1 is taken at 18:00")
        for hour in (8, 10):
            book_court(court=self.court2, start=local(self.day, hour), member=self.member)
        self.assertContains(self.book_form("club", hour=12), "reached the limit of 2 bookings")

    def test_gold_member_free_hour_needs_no_payment(self):
        gold_user = User.objects.create(username="g", email="g@example.com", role="member")
        gold = self.make_member(self.gold, name="Gold Gita")
        gold.user = gold_user
        gold.save()
        self.client.force_login(gold_user)
        self.assertContains(self.client.get(self.confirm_url()), "Confirm free session")
        self.book_form("online")  # even if "online" is posted, a free session is simply confirmed
        self.assertEqual(Booking.objects.get().status, "confirmed")


class PortalMyBookingsTests(PortalCase):
    def test_member_sees_only_their_own_bookings(self):
        mine = book_court(court=self.court1, start=local(self.day, 18), member=self.member)
        other = self.make_member(self.silver, name="Someone Else")
        book_court(court=self.court2, start=local(self.day, 19), member=other)
        page = self.client.get(reverse("portal_mine"))
        self.assertContains(page, "Court 1")
        self.assertNotContains(page, "Court 2")
        self.assertEqual(self.client.post(reverse("portal_cancel", args=[Booking.objects.get(member=other).pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("portal_pay", args=[Booking.objects.get(member=other).pk])).status_code, 404)
        self.assertEqual(Booking.objects.get(pk=mine.pk).status, "confirmed")

    def test_cancelling_a_paid_booking_a_week_ahead_refunds_it(self):
        booking = book_court(court=self.court1, start=local(self.day, 18), member=self.member, payment_method="upi")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("portal_cancel", args=[booking.pk]), follow=True)
        self.assertContains(response, "refund of ₹680")
        self.assertEqual(Ledger.objects.filter(kind="refund").get().amount_paise, -68000)
        self.assertEqual(len(mail.outbox), 1)  # the cancellation email

    def test_releasing_a_held_slot(self):
        booking = book_court(court=self.court1, start=local(self.day, 18), member=self.member, hold=True)
        self.client.post(reverse("portal_cancel", args=[booking.pk]))
        booking.refresh_from_db()
        self.assertEqual(booking.status, "cancelled")

    @patch("courts.portal.start_online_payment")
    def test_pay_now_for_an_unpaid_booking_opens_checkout(self, start):
        booking = book_court(court=self.court1, start=local(self.day, 18), member=self.member)
        start.return_value = Payment.objects.create(razorpay_order_id="o2", source="court", reference_id=booking.pk, amount_paise=68000)
        response = self.client.post(reverse("portal_pay", args=[booking.pk]))
        self.assertEqual(response.status_code, 302)

    def test_member_can_open_the_payment_page_for_their_own_booking_only(self):
        booking = book_court(court=self.court1, start=local(self.day, 18), member=self.member, hold=True)
        mine = Payment.objects.create(razorpay_order_id="o3", source="court", reference_id=booking.pk, amount_paise=68000)
        self.assertEqual(self.client.get(reverse("pay_page", args=[mine.pk])).status_code, 200)
        stranger = User.objects.create(username="s", email="s@example.com", role="member")
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(reverse("pay_page", args=[mine.pk])).status_code, 403)
