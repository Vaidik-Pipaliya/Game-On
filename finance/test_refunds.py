from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from courts.services import book_court, cancel_booking
from courts.tests import NOW, Fixtures, at
from shop.models import Product, Variant
from shop.services import cancel_order, place_order

from .models import Ledger, Payment
from .services import mark_payment_captured, refund_gateway_payment, refunds_waiting, retry_failed_refunds

KEYS = override_settings(RAZORPAY_KEY_ID="rzp_test_abc", RAZORPAY_KEY_SECRET="secret", CRON_SECRET="cron-secret")


class RefundCase(Fixtures, TestCase):
    def setUp(self):
        self.make_world()
        self.gateway_patch = patch("finance.services._razorpay_client")
        self.gateway = self.gateway_patch.start().return_value
        self.gateway.payment.refund.return_value = {"id": "rfnd_1"}
        self.addCleanup(self.gateway_patch.stop)

    def paid_online_booking(self, hour=18):
        """A booking that was paid through Razorpay (order -> captured payment)."""
        booking = self.book(self.court1, at(10, hour), guest_name="G", guest_phone="1", payment_method="online")
        payment = Payment.objects.create(razorpay_order_id=f"order_{hour}", source="court", reference_id=booking.pk, amount_paise=80000)
        mark_payment_captured(razorpay_order_id=payment.razorpay_order_id, razorpay_payment_id=f"pay_{hour}")
        booking.refresh_from_db()
        payment.refresh_from_db()
        return booking, payment

    def cancel(self, booking, now=NOW):
        with self.captureOnCommitCallbacks(execute=True):
            return cancel_booking(booking, now=now)


class CancellationRefundTests(RefundCase):
    def test_cancelling_an_online_paid_booking_refunds_through_razorpay(self):
        booking, payment = self.paid_online_booking()
        self.cancel(booking)
        self.gateway.payment.refund.assert_called_once()
        args = self.gateway.payment.refund.call_args.args
        self.assertEqual((args[0], args[1]["amount"]), ("pay_18", 80000))
        payment.refresh_from_db()
        self.assertEqual((payment.refund_id, payment.refunded_paise, payment.refund_error), ("rfnd_1", 80000, ""))
        self.assertEqual(Ledger.objects.get(kind="refund").amount_paise, -80000)
        self.assertEqual(refunds_waiting().count(), 0)

    def test_no_gateway_refund_inside_the_24_hour_window_or_for_desk_payments(self):
        booking, _ = self.paid_online_booking()
        self.cancel(booking, now=at(10, 9))  # 9 hours before: no refund
        cash = self.book(self.court2, at(10, 19), guest_name="C", guest_phone="2", payment_method="cash")
        self.cancel(cash)  # refundable, but paid at the desk: the club hands cash back, not Razorpay
        self.gateway.payment.refund.assert_not_called()

    def test_gateway_failure_never_blocks_the_cancellation_and_is_kept_for_retry(self):
        booking, payment = self.paid_online_booking()
        self.gateway.payment.refund.side_effect = ConnectionError("Razorpay unreachable")
        result = self.cancel(booking)
        booking.refresh_from_db()
        self.assertEqual((booking.status, result.refund_paise), ("cancelled", 80000))
        self.assertEqual(Ledger.objects.filter(kind="refund").count(), 1)  # the books already say it's refunded
        payment.refresh_from_db()
        self.assertEqual((payment.refund_id, payment.refund_error), ("", "Razorpay unreachable"))
        self.assertEqual(refunds_waiting().count(), 1)

    def test_retry_sends_it_once_and_never_twice(self):
        booking, payment = self.paid_online_booking()
        self.gateway.payment.refund.side_effect = ConnectionError("down")
        self.cancel(booking)
        self.gateway.payment.refund.side_effect = None
        self.assertEqual(retry_failed_refunds(), 1)
        self.assertEqual(retry_failed_refunds(), 0)
        self.assertTrue(refund_gateway_payment(payment.pk))  # calling it again is a no-op
        self.assertEqual(self.gateway.payment.refund.call_count, 2)  # the failed try + the successful retry only
        payment.refresh_from_db()
        self.assertEqual(payment.refund_id, "rfnd_1")

    def test_a_refund_owed_is_remembered_even_if_the_request_never_went_out(self):
        booking, payment = self.paid_online_booking()
        cancel_booking(booking, now=NOW)  # on_commit callbacks are NOT executed here: simulates a crash after commit
        payment.refresh_from_db()
        self.assertEqual(payment.refund_error, "Waiting to be sent to Razorpay")
        self.assertEqual(refunds_waiting().count(), 1)
        self.assertEqual(retry_failed_refunds(), 1)


class OtherRefundPathTests(RefundCase):
    def test_late_payment_for_a_lost_slot_is_returned_automatically(self):
        member = self.make_member(self.silver)
        held = book_court(court=self.court1, start=at(10, 18), member=member, hold=True, now=NOW)
        book_court(court=self.court1, start=at(10, 18), guest_name="B", guest_phone="2", now=NOW.replace(minute=30))  # after the hold expired
        payment = Payment.objects.create(razorpay_order_id="o9", source="court", reference_id=held.pk, amount_paise=68000)
        with self.captureOnCommitCallbacks(execute=True):
            mark_payment_captured(razorpay_order_id="o9", razorpay_payment_id="pay_9")
        self.assertEqual(self.gateway.payment.refund.call_args.args[0], "pay_9")
        payment.refresh_from_db()
        self.assertEqual(payment.refund_id, "rfnd_1")

    def test_duplicate_payment_refunds_only_the_second_one(self):
        member = self.make_member(self.silver)
        held = book_court(court=self.court1, start=at(10, 18), member=member, hold=True, now=NOW)
        first = Payment.objects.create(razorpay_order_id="a", source="court", reference_id=held.pk, amount_paise=68000)
        second = Payment.objects.create(razorpay_order_id="b", source="court", reference_id=held.pk, amount_paise=68000)
        with self.captureOnCommitCallbacks(execute=True):
            mark_payment_captured(razorpay_order_id="a", razorpay_payment_id="pay_a")
            mark_payment_captured(razorpay_order_id="b", razorpay_payment_id="pay_b")
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.refund_id, second.refund_id), ("", "rfnd_1"))

    def test_cancelling_an_online_paid_shop_order_refunds_through_razorpay(self):
        variant = Variant.objects.create(product=Product.objects.create(name="Ball", category="ball", price_paise=50000), stock=5)
        order, _ = place_order(items=[(variant, 1)], channel="online", customer_name="Asha", payment_method="online")
        payment = Payment.objects.create(razorpay_order_id="s1", source="shop", reference_id=order.pk, amount_paise=order.total_paise)
        mark_payment_captured(razorpay_order_id="s1", razorpay_payment_id="pay_s1")
        with self.captureOnCommitCallbacks(execute=True):
            cancel_order(order)
        self.assertEqual(self.gateway.payment.refund.call_args.args[1]["amount"], 50000)
        payment.refresh_from_db()
        self.assertEqual(payment.refund_id, "rfnd_1")


@KEYS
class RefundScreenTests(RefundCase):
    def test_owner_sees_waiting_refunds_and_can_send_them(self):
        booking, _ = self.paid_online_booking()
        self.gateway.payment.refund.side_effect = ConnectionError("down")
        self.cancel(booking)
        owner = User.objects.create(username="o", email="o@example.com", role="owner")
        web = Client()
        web.force_login(owner)
        self.assertContains(web.get(reverse("owner_dashboard")), "1 online refund")
        self.gateway.payment.refund.side_effect = None
        response = web.post(reverse("retry_refunds"), follow=True)
        self.assertContains(response, "Nothing is waiting")
        self.assertNotContains(web.get(reverse("owner_dashboard")), "online refund")
        web.force_login(User.objects.create(username="d", email="d@example.com", role="front_desk"))
        self.assertEqual(web.post(reverse("retry_refunds")).status_code, 403)

    def test_cron_job_retries_refunds(self):
        booking, _ = self.paid_online_booking()
        self.gateway.payment.refund.side_effect = ConnectionError("down")
        self.cancel(booking)
        self.gateway.payment.refund.side_effect = None
        reply = Client().get(reverse("cron", args=["retry-refunds"]), HTTP_AUTHORIZATION="Bearer cron-secret")
        self.assertEqual(reply.json(), {"job": "retry-refunds", "result": 1})
