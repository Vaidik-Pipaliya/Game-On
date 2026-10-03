import hashlib
import hmac
import json
from datetime import date
from unittest.mock import patch

from django.db.models import Sum
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.models import User
from courts.models import Booking
from courts.services import SlotTaken, cancel_booking, take_booking_payment
from courts.tests import NOW, Fixtures, ParallelMixin, at
from members.services import register_member, renew_membership

from .models import Ledger, Payment
from .services import (
    OnlinePaymentUnavailable, checkout_signature_is_valid, mark_payment_captured, record_payment,
    record_refund, start_online_payment, webhook_signature_is_valid,
)

KEYS = dict(RAZORPAY_KEY_ID="rzp_test_abc", RAZORPAY_KEY_SECRET="key-secret", RAZORPAY_WEBHOOK_SECRET="hook-secret")


def sign(secret, message):
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def ledger_total(**filters):
    return Ledger.objects.filter(**filters).aggregate(total=Sum("amount_paise"))["total"] or 0


class LedgerTests(TestCase):
    def test_payment_is_positive_and_refund_is_negative(self):
        record_payment(source="court", method="cash", amount_paise=68000)
        record_refund(source="court", method="cash", amount_paise=68000)
        self.assertEqual(list(Ledger.objects.order_by("id").values_list("amount_paise", flat=True)), [68000, -68000])
        self.assertEqual(ledger_total(), 0)

    def test_zero_or_negative_amounts_are_rejected(self):
        with self.assertRaises(ValueError):
            record_payment(source="court", method="cash", amount_paise=0)
        with self.assertRaises(ValueError):
            record_refund(source="court", method="cash", amount_paise=-5)

    def test_ledger_rows_cannot_be_edited_or_deleted(self):
        row = record_payment(source="bar", method="upi", amount_paise=1000)
        row.amount_paise = 1
        with self.assertRaises(TypeError):
            row.save()
        with self.assertRaises(TypeError):
            row.delete()
        with self.assertRaises(TypeError):
            Ledger.objects.filter(pk=row.pk).update(amount_paise=1)
        with self.assertRaises(TypeError):
            Ledger.objects.all().delete()
        self.assertEqual(Ledger.objects.get().amount_paise, 1000)


class BookingMoneyTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()

    def test_cash_booking_writes_one_ledger_row_with_source_and_method(self):
        booking = self.book(self.court1, at(10, 18), member=self.make_member(self.silver), payment_method="cash")
        row = Ledger.objects.get()
        self.assertEqual((row.source, row.method, row.amount_paise, row.reference_id), ("court", "cash", 68000, booking.pk))
        self.assertTrue(booking.is_paid)

    def test_free_session_is_paid_without_a_ledger_row(self):
        booking = self.book(self.court1, at(10, 18), member=self.make_member(self.gold), payment_method="cash")
        self.assertEqual((booking.price_paise, booking.is_paid, Ledger.objects.count()), (0, True, 0))

    def test_online_booking_stays_unpaid_until_razorpay_confirms(self):
        booking = self.book(self.court1, at(10, 18), guest_name="G", guest_phone="1", payment_method="online")
        self.assertEqual((booking.is_paid, booking.payment_method, Ledger.objects.count()), (False, "online", 0))

    def test_slot_taken_leaves_no_money_behind(self):
        self.book(self.court1, at(10, 18), guest_name="A", guest_phone="1", payment_method="cash")
        with self.assertRaises(SlotTaken):
            self.book(self.court1, at(10, 18), guest_name="B", guest_phone="2", payment_method="cash")
        self.assertEqual(Ledger.objects.count(), 1)  # the failed booking's payment was rolled back with it

    def test_refund_reverses_the_payment_so_court_revenue_is_zero(self):
        booking = self.book(self.court1, at(10, 18), guest_name="G", guest_phone="1", payment_method="upi")
        cancel_booking(booking, now=NOW)
        self.assertEqual(ledger_total(source="court"), 0)
        refund = Ledger.objects.get(kind="refund")
        self.assertEqual((refund.method, refund.amount_paise), ("upi", -80000))

    def test_late_cancel_keeps_the_money(self):
        booking = self.book(self.court1, at(10, 18), guest_name="G", guest_phone="1", payment_method="cash")
        cancel_booking(booking, now=at(10, 9))
        self.assertEqual(ledger_total(source="court"), 80000)

    def test_unpaid_booking_cancel_refunds_nothing(self):
        booking = self.book(self.court1, at(10, 18), guest_name="G", guest_phone="1")
        self.assertEqual(cancel_booking(booking, now=NOW).refund_paise, 0)
        self.assertEqual(Ledger.objects.count(), 0)

    def test_take_payment_later_only_once(self):
        booking = self.book(self.court1, at(10, 18), guest_name="G", guest_phone="1")
        take_booking_payment(booking, "card")
        with self.assertRaisesMessage(Exception, "already paid"):
            take_booking_payment(booking, "card")
        self.assertEqual(ledger_total(), 80000)


class MembershipMoneyTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()

    def test_registration_and_renewal_record_the_plan_fee(self):
        member = register_member(
            full_name="New", phone="9000000099", email="", date_of_birth=date(1990, 1, 1),
            plan=self.gold, payment_method="card", today=date(2026, 10, 3),
        )
        renew_membership(member, payment_method="upi", today=date(2026, 10, 3))
        self.assertEqual(ledger_total(source="membership"), 2 * self.gold.price_paise)
        self.assertEqual(sorted(Ledger.objects.values_list("method", flat=True)), ["card", "upi"])


@override_settings(**KEYS)
class RazorpayTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()
        self.booking = self.book(self.court1, at(10, 18), guest_name="G", guest_phone="1", payment_method="online")
        self.payment = Payment.objects.create(
            razorpay_order_id="order_1", source="court", reference_id=self.booking.pk, amount_paise=80000
        )

    def test_order_is_created_through_the_sdk(self):
        with patch("finance.services._razorpay_client") as client:
            client.return_value.order.create.return_value = {"id": "order_new"}
            payment = start_online_payment(source="court", reference_id=self.booking.pk, amount_paise=80000)
        self.assertEqual(payment.razorpay_order_id, "order_new")
        self.assertEqual(client.return_value.order.create.call_args[0][0]["amount"], 80000)

    def test_gateway_failure_becomes_a_friendly_error(self):
        with patch("finance.services._razorpay_client") as client:
            client.return_value.order.create.side_effect = ConnectionError("down")
            with self.assertRaises(OnlinePaymentUnavailable):
                start_online_payment(source="court", reference_id=1, amount_paise=100)

    @override_settings(RAZORPAY_KEY_ID="")
    def test_missing_keys_become_a_friendly_error(self):
        with self.assertRaises(OnlinePaymentUnavailable):
            start_online_payment(source="court", reference_id=1, amount_paise=100)

    def test_checkout_signature(self):
        good = sign("key-secret", b"order_1|pay_1")
        self.assertTrue(checkout_signature_is_valid("order_1", "pay_1", good))
        self.assertFalse(checkout_signature_is_valid("order_1", "pay_2", good))  # replayed for another payment
        self.assertFalse(checkout_signature_is_valid("order_1", "pay_1", "forged"))
        self.assertFalse(checkout_signature_is_valid("order_1", "pay_1", None))

    def test_webhook_signature(self):
        body = b'{"event":"payment.captured"}'
        self.assertTrue(webhook_signature_is_valid(body, sign("hook-secret", body)))
        self.assertFalse(webhook_signature_is_valid(body + b" ", sign("hook-secret", body)))  # tampered body

    def test_capture_marks_booking_paid_and_writes_ledger_once(self):
        self.assertTrue(mark_payment_captured(razorpay_order_id="order_1", razorpay_payment_id="pay_1"))
        self.assertFalse(mark_payment_captured(razorpay_order_id="order_1", razorpay_payment_id="pay_1"))  # replay
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.is_paid)
        self.assertEqual(Ledger.objects.filter(source="court", method="online").count(), 1)

    def test_capture_with_wrong_amount_is_ignored(self):
        self.assertFalse(mark_payment_captured(razorpay_order_id="order_1", razorpay_payment_id="pay_1", amount_paise=1))
        self.assertEqual(Ledger.objects.count(), 0)

    def test_capture_for_unknown_order_is_ignored(self):
        self.assertFalse(mark_payment_captured(razorpay_order_id="order_x", razorpay_payment_id="pay_9"))


@override_settings(**KEYS)
class RazorpayEndpointTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()
        self.client.force_login(User.objects.create(username="d", email="d@example.com", role="front_desk"))
        booking = self.book(self.court1, at(10, 18), guest_name="G", guest_phone="1", payment_method="online")
        self.payment = Payment.objects.create(
            razorpay_order_id="order_1", source="court", reference_id=booking.pk, amount_paise=80000
        )

    def webhook(self, body, signature):
        return self.client.post(
            reverse("razorpay_webhook"), body, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=signature
        )

    def captured_event(self, amount=80000):
        return json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {
            "id": "pay_1", "order_id": "order_1", "amount": amount}}}}).encode()

    def test_pay_page_shows_public_key_but_never_the_secret(self):
        response = self.client.get(reverse("pay_page", args=[self.payment.pk]))
        self.assertContains(response, "rzp_test_abc")
        self.assertNotContains(response, "key-secret")
        self.assertNotContains(response, "hook-secret")

    def test_verify_with_valid_signature_records_payment(self):
        response = self.client.post(reverse("pay_verify", args=[self.payment.pk]), {
            "razorpay_payment_id": "pay_1", "razorpay_signature": sign("key-secret", b"order_1|pay_1"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Ledger.objects.count(), 1)

    def test_verify_with_forged_signature_records_nothing(self):
        response = self.client.post(reverse("pay_verify", args=[self.payment.pk]), {
            "razorpay_payment_id": "pay_1", "razorpay_signature": "forged",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Ledger.objects.count(), 0)

    def test_webhook_is_idempotent_when_razorpay_retries(self):
        body = self.captured_event()
        for _ in range(3):
            self.assertEqual(self.webhook(body, sign("hook-secret", body)).status_code, 200)
        self.assertEqual(Ledger.objects.count(), 1)
        self.assertTrue(Booking.objects.get(pk=self.payment.reference_id).is_paid)

    def test_webhook_after_checkout_callback_does_not_double_count(self):
        self.client.post(reverse("pay_verify", args=[self.payment.pk]), {
            "razorpay_payment_id": "pay_1", "razorpay_signature": sign("key-secret", b"order_1|pay_1"),
        })
        body = self.captured_event()
        self.webhook(body, sign("hook-secret", body))
        self.assertEqual(Ledger.objects.count(), 1)

    def test_webhook_with_bad_signature_is_rejected(self):
        body = self.captured_event()
        self.assertEqual(self.webhook(body, "forged").status_code, 400)
        self.assertEqual(Ledger.objects.count(), 0)

    def test_webhook_needs_no_login_or_csrf(self):
        strict = Client(enforce_csrf_checks=True)  # the default test client skips CSRF, so use a strict one
        body = self.captured_event()
        response = strict.post(reverse("razorpay_webhook"), body, content_type="application/json",
                               HTTP_X_RAZORPAY_SIGNATURE=sign("hook-secret", body))
        self.assertEqual(response.status_code, 200)

    def test_verify_endpoint_does_require_csrf(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(User.objects.get(username="d"))
        response = strict.post(reverse("pay_verify", args=[self.payment.pk]), {"razorpay_payment_id": "pay_1"})
        self.assertEqual(response.status_code, 403)

    def test_member_role_cannot_open_pay_page(self):
        self.client.force_login(User.objects.create(username="m", email="m@example.com", role="member"))
        self.assertEqual(self.client.get(reverse("pay_page", args=[self.payment.pk])).status_code, 403)


class CaptureConcurrencyTests(ParallelMixin, Fixtures, TransactionTestCase):
    def setUp(self):
        self.make_world()
        booking = self.book(self.court1, at(10, 18), guest_name="G", guest_phone="1", payment_method="online")
        Payment.objects.create(razorpay_order_id="order_1", source="court", reference_id=booking.pk, amount_paise=80000)

    def test_ten_simultaneous_captures_write_one_ledger_row(self):
        jobs = [lambda: mark_payment_captured(razorpay_order_id="order_1", razorpay_payment_id="pay_1")] * 10
        self.assertEqual(self.run_parallel(jobs), ["ok"] * 10)  # nobody crashes...
        self.assertEqual(Ledger.objects.count(), 1)  # ...and the money is counted once
