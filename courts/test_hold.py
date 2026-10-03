from datetime import timedelta

from django.core import mail
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase

from accounts.models import AuditLog
from finance.models import Ledger, Payment
from finance.services import mark_payment_captured
from courts.models import Booking
from courts.services import (
    DailyLimitReached, SlotTaken, apply_online_payment, book_court, grid_for_day, release_expired_holds, release_hold,
)

from .tests import NOW, Fixtures, ParallelMixin, at

LATER = NOW + timedelta(minutes=6)  # the hold (5 minutes) has run out by then


class HoldFixtures(Fixtures):
    def hold(self, member=None, hour=18, court=None, now=NOW, **kwargs):
        kwargs.setdefault("guest_name", "G" if member is None else "")
        kwargs.setdefault("guest_phone", "9000000001" if member is None else "")
        return book_court(court=court or self.court1, start=at(10, hour), member=member, hold=True, now=now, **kwargs)

    def pay(self, booking, order_id="order_1", payment_id="pay_1"):
        Payment.objects.create(razorpay_order_id=order_id, source="court", reference_id=booking.pk, amount_paise=booking.price_paise)
        return mark_payment_captured(razorpay_order_id=order_id, razorpay_payment_id=payment_id)


class HoldTests(HoldFixtures, TestCase):
    def setUp(self):
        self.make_world()

    def test_hold_reserves_the_slot_for_five_minutes_without_taking_money(self):
        booking = self.hold()
        self.assertEqual((booking.status, booking.is_paid, Ledger.objects.count()), ("held", False, 0))
        self.assertEqual(booking.hold_expires_at, NOW + timedelta(minutes=5))

    def test_a_held_slot_blocks_everyone_else_and_shows_as_taken(self):
        self.hold()
        with self.assertRaises(SlotTaken):
            self.book(self.court1, at(10, 18, 30), guest_name="B", guest_phone="2")
        _, rows = grid_for_day(at(10, 0).date(), now=NOW)
        cell = next(c for r in rows if r["court"] == self.court1 for c in r["cells"] if c["start"] == at(10, 18))
        self.assertEqual(cell["state"], "taken")

    def test_held_bookings_count_toward_the_daily_limit(self):
        member = self.make_member(self.silver)
        self.hold(member, hour=8)
        self.hold(member, hour=10)
        with self.assertRaises(DailyLimitReached):
            self.hold(member, hour=12)

    def test_free_session_is_confirmed_straight_away_no_payment_needed(self):
        gold = self.make_member(self.gold)  # first two hours a month are free
        booking = self.hold(gold)
        self.assertEqual((booking.status, booking.is_paid, booking.price_paise), ("confirmed", True, 0))

    def test_expired_hold_frees_the_slot_for_the_next_person(self):
        first = self.hold()
        second = book_court(court=self.court1, start=at(10, 18), guest_name="B", guest_phone="2", now=LATER)
        first.refresh_from_db()
        self.assertEqual((first.status, second.status), ("cancelled", "confirmed"))

    def test_expired_holds_are_released_in_bulk(self):
        self.hold()
        self.hold(court=self.court2)
        self.assertEqual(release_expired_holds(NOW + timedelta(minutes=1)), 0)
        self.assertEqual(release_expired_holds(LATER), 2)

    def test_member_can_release_their_own_hold_immediately(self):
        booking = self.hold()
        self.assertTrue(release_hold(booking))
        self.book(self.court1, at(10, 18), guest_name="B", guest_phone="2")  # no SlotTaken
        self.assertFalse(release_hold(booking))  # already released


class HoldPaymentTests(HoldFixtures, TestCase):
    def setUp(self):
        self.make_world()
        self.member = self.make_member(self.silver)
        self.member.email = "m@example.com"
        self.member.save()

    def test_payment_confirms_the_held_booking_and_sends_the_message(self):
        booking = self.hold(self.member)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(self.pay(booking))
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.is_paid, booking.payment_method, booking.hold_expires_at), ("confirmed", True, "online", None))
        self.assertEqual(Ledger.objects.get().amount_paise, 68000)
        self.assertEqual(len(mail.outbox), 1)

    def test_late_payment_reconfirms_if_the_slot_is_still_free(self):
        booking = self.hold(self.member)
        release_expired_holds(LATER)
        self.pay(booking)
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.is_paid), ("confirmed", True))

    def test_late_payment_is_refunded_if_someone_else_took_the_slot(self):
        booking = self.hold(self.member)
        book_court(court=self.court1, start=at(10, 18), guest_name="B", guest_phone="2", now=LATER)  # releases + takes it
        self.pay(booking)
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.is_paid), ("cancelled", False))
        self.assertEqual(Ledger.objects.filter(source="court").aggregate(t=Sum("amount_paise"))["t"], 0)  # +68000 then -68000
        self.assertEqual(AuditLog.objects.get().action, "booking.payment_refunded")
        self.assertIn("Razorpay dashboard", Ledger.objects.get(kind="refund").note)

    def test_second_payment_for_an_already_paid_booking_is_refunded(self):
        booking = self.hold(self.member)
        self.pay(booking)
        self.pay(booking, order_id="order_2", payment_id="pay_2")
        self.assertEqual(Ledger.objects.filter(kind="refund").count(), 1)
        self.assertEqual(Ledger.objects.aggregate(t=Sum("amount_paise"))["t"], 68000)  # paid once, net
        booking.refresh_from_db()
        self.assertEqual(booking.status, "confirmed")

    def test_staff_created_online_booking_is_just_marked_paid(self):
        booking = self.book(self.court1, at(10, 18), member=self.member, payment_method="online")
        self.assertEqual(apply_online_payment(booking.pk, Payment(amount_paise=68000)), "paid")


class HoldRaceTests(ParallelMixin, HoldFixtures, TransactionTestCase):
    def setUp(self):
        self.make_world()

    def test_twenty_members_racing_to_hold_one_slot_get_exactly_one(self):
        members = [self.make_member(self.silver) for _ in range(20)]
        results = self.run_parallel([lambda m=m: self.hold(m) for m in members])
        self.assertEqual((results.count("ok"), results.count("SlotTaken")), (1, 19))
        self.assertEqual(Booking.objects.filter(status="held").count(), 1)
