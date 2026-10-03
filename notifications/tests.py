from datetime import date, timedelta
from unittest.mock import patch

import requests
from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from courts.models import Booking
from courts.services import book_court, cancel_booking
from courts.tests import NOW, Fixtures, at
from members.models import Membership
from members.services import send_renewal_reminders
from shop.models import Product, Variant
from shop.services import place_order

from .models import Notification
from .services import retry_failed, send_booking_reminders

WHATSAPP_ON = override_settings(WHATSAPP_TOKEN="test-token", WHATSAPP_PHONE_NUMBER_ID="12345")


class NotifyFixtures(Fixtures):
    def make_people(self):
        self.make_world()
        self.opted_in = self.make_member(self.silver, name="Asha")
        self.opted_in.whatsapp_opt_in, self.opted_in.email = True, "asha@example.com"
        self.opted_in.save()
        self.email_only = self.make_member(self.silver, name="Ravi")
        self.email_only.email = "ravi@example.com"
        self.email_only.save()

    def book_and_commit(self, member=None, hour=18, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return book_court(court=self.court1, start=at(10, hour), member=member, now=NOW, **kwargs)


class BookingMessageTests(NotifyFixtures, TestCase):
    def setUp(self):
        self.make_people()

    @WHATSAPP_ON
    @patch("notifications.services.requests.post")
    def test_opted_in_member_gets_whatsapp_template(self, post):
        booking = self.book_and_commit(self.opted_in)
        note = Notification.objects.get()
        self.assertEqual((note.channel, note.status, note.template, note.booking), ("whatsapp", "sent", "booking_confirmed", booking))
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["to"], f"91{self.opted_in.phone}")
        self.assertEqual(payload["template"]["name"], "booking_confirmed")
        params = [p["text"] for p in payload["template"]["components"][0]["parameters"]]
        self.assertEqual(params, ["Court 1", "Sat 10 Oct", "6:00 PM"])
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-token")

    def test_member_without_opt_in_gets_email(self):
        self.book_and_commit(self.email_only)
        self.assertEqual(Notification.objects.get().channel, "email")
        self.assertIn("Court 1", mail.outbox[0].body)

    def test_whatsapp_off_falls_back_to_email_even_if_opted_in(self):
        self.book_and_commit(self.opted_in)  # no token configured in tests
        self.assertEqual(Notification.objects.get().channel, "email")

    @WHATSAPP_ON
    @patch("notifications.services.requests.post", side_effect=requests.ConnectionError("Meta down"))
    def test_whatsapp_failure_is_logged_and_email_sent_instead(self, post):
        self.book_and_commit(self.opted_in)
        whatsapp = Notification.objects.get(channel="whatsapp")
        self.assertEqual((whatsapp.status, whatsapp.attempts), ("failed", 1))
        self.assertIn("Meta down", whatsapp.error)
        self.assertEqual(Notification.objects.get(channel="email").status, "sent")
        self.assertEqual(len(mail.outbox), 1)

    @WHATSAPP_ON
    @patch("notifications.services.requests.post")
    def test_meta_rejection_reason_is_saved(self, post):
        post.return_value.ok = False
        post.return_value.status_code = 400
        post.return_value.json.return_value = {"error": {
            "message": "(#131030) Recipient phone number not in allowed list", "code": 131030,
            "error_data": {"details": "Add the number to the allowed list"},
        }}
        self.book_and_commit(self.opted_in)
        error = Notification.objects.get(channel="whatsapp").error
        self.assertIn("Recipient phone number not in allowed list", error)
        self.assertIn("131030", error)
        self.assertEqual(Notification.objects.get(channel="email").status, "sent")

    def test_nothing_is_sent_if_the_booking_rolls_back(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    book_court(court=self.court1, start=at(10, 18), member=self.email_only, now=NOW)
                    raise RuntimeError("something later in the request failed")
            except RuntimeError:
                pass
        self.assertEqual((len(callbacks), Notification.objects.count(), len(mail.outbox)), (0, 0, 0))

    def test_walk_in_guests_get_no_messages(self):
        self.book_and_commit(guest_name="Guest", guest_phone="9000000000")
        self.assertEqual(Notification.objects.count(), 0)

    def test_cancellation_message(self):
        booking = self.book_and_commit(self.email_only)
        with self.captureOnCommitCallbacks(execute=True):
            cancel_booking(booking, now=NOW)
        self.assertEqual(Notification.objects.filter(template="booking_cancelled").count(), 1)

    def test_reminder_once_for_sessions_in_the_next_two_hours(self):
        soon = self.book_and_commit(self.email_only, hour=18)
        self.book_and_commit(self.email_only, hour=21)  # too far ahead
        self.assertEqual(send_booking_reminders(now=at(10, 16, 30)), 1)
        self.assertEqual(send_booking_reminders(now=at(10, 17)), 0)  # already reminded
        self.assertEqual(Notification.objects.get(template="booking_reminder").booking, soon)


class RetryTests(NotifyFixtures, TestCase):
    def setUp(self):
        self.make_people()

    def test_failed_email_is_retried_until_it_works_and_stops_after_three_attempts(self):
        with patch("notifications.services.send_mail", side_effect=OSError("SMTP down")):
            self.book_and_commit(self.email_only)
            self.assertEqual(retry_failed(), 0)
        note = Notification.objects.get()
        self.assertEqual((note.status, note.attempts), ("failed", 2))
        self.assertEqual(retry_failed(), 1)  # SMTP is back
        note.refresh_from_db()
        self.assertEqual((note.status, note.attempts), ("sent", 3))

    def test_gives_up_after_max_attempts(self):
        with patch("notifications.services.send_mail", side_effect=OSError("SMTP down")):
            self.book_and_commit(self.email_only)
            retry_failed()
            retry_failed()
            self.assertEqual(retry_failed(), 0)
        self.assertEqual(Notification.objects.get().attempts, 3)

    def test_log_screen_and_retry_button(self):
        with patch("notifications.services.send_mail", side_effect=OSError("SMTP down")):
            self.book_and_commit(self.email_only)
        note = Notification.objects.get()
        self.client.force_login(User.objects.create(username="b", email="b@example.com", role="bar_staff"))
        self.assertEqual(self.client.get(reverse("notification_log")).status_code, 403)
        self.client.force_login(User.objects.create(username="d", email="d@example.com", role="front_desk"))
        self.assertContains(self.client.get(reverse("notification_log")), "SMTP down")
        self.client.post(reverse("notification_retry", args=[note.pk]))
        note.refresh_from_db()
        self.assertEqual(note.status, "sent")


class OtherMessageTests(NotifyFixtures, TestCase):
    def setUp(self):
        self.make_people()

    def test_renewal_reminders_are_logged(self):
        Membership.objects.filter(member=self.email_only).update(end_date=date(2026, 10, 10))
        send_renewal_reminders(today=date(2026, 10, 3))
        note = Notification.objects.get(template="renewal_reminder")
        self.assertEqual((note.to, note.status), ("ravi@example.com", "sent"))

    def test_low_stock_emails_owner_and_shop_staff(self):
        User.objects.create(username="o", email="owner@example.com", role="owner")
        User.objects.create(username="s", email="shop@example.com", role="shop_staff")
        User.objects.create(username="b", email="bar@example.com", role="bar_staff")
        product = Product.objects.create(name="Grip", category="accessory", price_paise=10000)
        variant = Variant.objects.create(product=product, stock=3, reorder_level=2)
        with self.captureOnCommitCallbacks(execute=True):
            place_order(items=[(variant, 1)], channel="counter", payment_method="cash")
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["owner@example.com", "shop@example.com"])
        self.assertIn("Grip", mail.outbox[0].body)


@override_settings(CRON_SECRET="cron-secret")
class CronEndpointTests(NotifyFixtures, TestCase):
    def setUp(self):
        self.make_people()

    def call(self, job, secret="cron-secret", **params):
        return self.client.get(reverse("cron", args=[job]), params, HTTP_AUTHORIZATION=f"Bearer {secret}")

    def test_wrong_or_missing_secret_is_refused(self):
        self.assertEqual(self.call("retry-notifications", secret="guess").status_code, 403)
        self.assertEqual(self.client.get(reverse("cron", args=["retry-notifications"])).status_code, 403)

    @override_settings(CRON_SECRET="")
    def test_jobs_are_off_when_no_secret_is_configured(self):
        self.assertEqual(self.call("retry-notifications", secret="").status_code, 403)

    def test_unknown_job(self):
        self.assertEqual(self.call("delete-everything").status_code, 404)

    def test_daily_reminder_run_covers_the_whole_day(self):
        booking = Booking.objects.create(court=self.court1, member=self.email_only,
                                         start=timezone.now() + timedelta(hours=9), end=timezone.now() + timedelta(hours=10))
        self.assertEqual(self.call("booking-reminders").json()["result"], 0)  # default 2-hour window
        self.assertEqual(self.call("booking-reminders", window_hours=17).json()["result"], 1)
        self.assertEqual(Notification.objects.get(template="booking_reminder").booking, booking)

    def test_renewal_and_retry_jobs_run(self):
        self.assertEqual(self.call("renewal-reminders").json(), {"job": "renewal-reminders", "result": 0})
        self.assertEqual(self.call("retry-notifications").json()["result"], 0)
