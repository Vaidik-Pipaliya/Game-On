import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase

from members.models import Member, Membership, Plan

from .models import Booking, Court, Sport
from .services import DailyLimitReached, InvalidSlot, SlotTaken, book_court

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=IST)


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)


class Fixtures:
    """Shared setup: one tennis court at ₹800/hour, a Gold plan and helpers to make members."""

    def make_world(self):
        self.gold = Plan.objects.create(
            name="Gold", price_paise=1200000, court_discount_pct=30, free_court_hours_per_month=2, daily_booking_limit=2
        )
        self.silver = Plan.objects.create(name="Silver", price_paise=600000, court_discount_pct=15)
        sport = Sport.objects.create(name="Tennis")
        self.court1 = Court.objects.create(sport=sport, name="Court 1", walk_in_rate_paise=80000)
        self.court2 = Court.objects.create(sport=sport, name="Court 2", walk_in_rate_paise=80000)
        self._phone = 9000000000

    def make_member(self, plan=None, name="Member"):
        self._phone += 1
        member = Member.objects.create(full_name=name, phone=str(self._phone), date_of_birth=date(1990, 1, 1))
        if plan:
            Membership.objects.create(member=member, plan=plan, start_date=date(2026, 1, 1), end_date=date(2027, 12, 31))
        return member

    def book(self, court, start, **kwargs):
        return book_court(court=court, start=start, now=NOW, **kwargs)


class DatabaseConstraintTests(Fixtures, TestCase):
    """These bypass book_court() on purpose: the database must refuse bad rows by itself."""

    def setUp(self):
        self.make_world()

    def raw_booking(self, court, start, **kwargs):
        # Own savepoint, so a rejected insert doesn't break the surrounding test transaction.
        with transaction.atomic():
            return Booking.objects.create(
                court=court, guest_name="G", guest_phone="1", start=start, end=start + timedelta(hours=1), **kwargs
            )

    def test_overlapping_booking_on_same_court_is_rejected(self):
        self.raw_booking(self.court1, at(10, 18))
        with self.assertRaises(IntegrityError):
            self.raw_booking(self.court1, at(10, 18))

    def test_half_hour_offset_still_overlaps(self):
        self.raw_booking(self.court1, at(10, 18))
        with self.assertRaises(IntegrityError):
            self.raw_booking(self.court1, at(10, 18, 30))

    def test_back_to_back_sessions_are_allowed(self):
        self.raw_booking(self.court1, at(10, 18))
        self.raw_booking(self.court1, at(10, 19))
        self.assertEqual(Booking.objects.count(), 2)

    def test_same_time_on_another_court_is_allowed(self):
        self.raw_booking(self.court1, at(10, 18))
        self.raw_booking(self.court2, at(10, 18))
        self.assertEqual(Booking.objects.count(), 2)

    def test_cancelled_booking_does_not_block_the_slot(self):
        self.raw_booking(self.court1, at(10, 18), status=Booking.Status.CANCELLED)
        self.raw_booking(self.court1, at(10, 18))
        self.assertEqual(Booking.objects.count(), 2)

    def test_whole_court_booking_must_be_one_hour(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.create(
                court=self.court1, guest_name="G", guest_phone="1", start=at(10, 18), end=at(10, 20)
            )


class SlotValidationTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()

    def assertInvalid(self, start):
        with self.assertRaises(InvalidSlot):
            self.book(self.court1, start, guest_name="G", guest_phone="1")

    def test_start_must_be_on_the_hour_or_half_hour(self):
        self.assertInvalid(at(10, 18, 15))

    def test_before_opening_and_after_closing_are_rejected(self):
        self.assertInvalid(at(10, 5, 30))
        self.assertInvalid(at(10, 21, 30))  # would end 22:30

    def test_last_session_ending_at_closing_is_allowed(self):
        self.assertEqual(self.book(self.court1, at(10, 21), guest_name="G", guest_phone="1").end, at(10, 22))

    def test_past_session_is_rejected(self):
        self.assertInvalid(at(1, 8))

    def test_naive_datetime_is_rejected(self):
        self.assertInvalid(datetime(2026, 10, 10, 18, 0))

    def test_guest_needs_name_and_phone(self):
        with self.assertRaises(ValidationError):
            self.book(self.court1, at(10, 18), guest_name="Asha")


class DailyLimitTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()
        self.member = self.make_member(self.silver)

    def test_third_booking_in_a_day_is_rejected(self):
        self.book(self.court1, at(10, 8), member=self.member)
        self.book(self.court1, at(10, 10), member=self.member)
        with self.assertRaises(DailyLimitReached):
            self.book(self.court2, at(10, 12), member=self.member)
        self.assertEqual(Booking.objects.count(), 2)

    def test_cancelled_booking_frees_quota(self):
        first = self.book(self.court1, at(10, 8), member=self.member)
        self.book(self.court1, at(10, 10), member=self.member)
        Booking.objects.filter(pk=first.pk).update(status=Booking.Status.CANCELLED)
        self.book(self.court2, at(10, 12), member=self.member)  # no error

    def test_limit_is_per_day(self):
        self.book(self.court1, at(10, 8), member=self.member)
        self.book(self.court1, at(10, 10), member=self.member)
        self.book(self.court1, at(11, 8), member=self.member)  # next day is fine

    def test_walk_in_guests_are_not_limited(self):
        for hour in (8, 9, 10):
            self.book(self.court1, at(10, hour), guest_name="Walk-in", guest_phone="9")

    def test_other_members_are_not_affected(self):
        other = self.make_member(self.silver)
        self.book(self.court1, at(10, 8), member=self.member)
        self.book(self.court1, at(10, 10), member=self.member)
        self.book(self.court2, at(10, 12), member=other)


class SlotTakenTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()

    def test_second_booking_gets_friendly_slot_taken_error(self):
        self.book(self.court1, at(10, 18), guest_name="A", guest_phone="1")
        # The message names the time that was requested (18:30), which overlaps the 18:00 booking.
        with self.assertRaisesMessage(SlotTaken, "Court 1 is taken at 18:30. Pick another court or time."):
            self.book(self.court1, at(10, 18, 30), guest_name="B", guest_phone="2")
        self.assertEqual(Booking.objects.count(), 1)

    def test_failed_booking_does_not_use_up_daily_quota(self):
        member = self.make_member(self.silver)
        self.book(self.court1, at(10, 18), guest_name="A", guest_phone="1")
        for _ in range(3):
            with self.assertRaises(SlotTaken):
                self.book(self.court1, at(10, 18), member=member)
        self.book(self.court2, at(10, 18), member=member)  # still allowed


class PricingOnBookingTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()

    def test_walk_in_pays_full_and_silver_gets_discount(self):
        walk_in = self.book(self.court1, at(10, 8), guest_name="G", guest_phone="1")
        silver = self.book(self.court1, at(10, 10), member=self.make_member(self.silver))
        self.assertEqual((walk_in.price_paise, silver.price_paise), (80000, 68000))

    def test_gold_gets_two_free_hours_a_month_then_member_rate(self):
        gold = self.make_member(self.gold)
        prices = [self.book(self.court1, at(day, 8), member=gold).price_paise for day in (10, 11, 12)]
        self.assertEqual(prices, [0, 0, 56000])

    def test_cancelling_a_free_session_gives_the_free_hour_back(self):
        gold = self.make_member(self.gold)
        first = self.book(self.court1, at(10, 8), member=gold)
        self.book(self.court1, at(11, 8), member=gold)
        Booking.objects.filter(pk=first.pk).update(status=Booking.Status.CANCELLED)
        self.assertEqual(self.book(self.court1, at(12, 8), member=gold).price_paise, 0)

    def test_price_is_frozen_when_plan_changes_later(self):
        member = self.make_member(self.silver)
        booking = self.book(self.court1, at(10, 8), member=member)
        Membership.objects.filter(member=member).update(plan=self.gold)  # upgrade after booking
        booking.refresh_from_db()
        self.assertEqual(booking.price_paise, 68000)

    def test_membership_that_expires_before_the_session_gets_walk_in_rate(self):
        member = self.make_member(self.silver)
        Membership.objects.filter(member=member).update(end_date=date(2026, 10, 5))
        self.assertEqual(self.book(self.court1, at(10, 8), member=member).price_paise, 80000)


class ParallelMixin:
    """Run callables on separate threads at the same instant, each with its own database connection."""

    def run_parallel(self, jobs):
        """Run callables at the same instant; return 'ok' or the exception class name for each."""
        barrier = threading.Barrier(len(jobs))

        def worker(job):
            try:
                barrier.wait()  # everyone starts together
                job()
                return "ok"
            except ValidationError as error:
                return type(error).__name__
            finally:
                connection.close()  # each thread opened its own connection

        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            return list(pool.map(worker, jobs))


class ConcurrencyTests(ParallelMixin, Fixtures, TransactionTestCase):
    """Real threads and real connections: this is what the constraint and the row lock are for."""

    def setUp(self):
        self.make_world()

    def test_fifty_simultaneous_requests_for_one_slot_create_exactly_one_booking(self):
        members = [self.make_member(self.silver) for _ in range(50)]
        court, start = self.court2, at(10, 18)
        results = self.run_parallel(
            [lambda m=m: book_court(court=court, start=start, member=m, now=NOW) for m in members]
        )
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("SlotTaken"), 49)
        self.assertEqual(Booking.objects.filter(court=court).count(), 1)

    def test_one_member_racing_for_five_slots_gets_only_two(self):
        member = self.make_member(self.silver)
        hours = [8, 9, 10, 11, 12]
        results = self.run_parallel(
            [lambda h=h: book_court(court=self.court1, start=at(10, h), member=member, now=NOW) for h in hours]
        )
        self.assertEqual(results.count("ok"), 2)
        self.assertEqual(results.count("DailyLimitReached"), 3)
        self.assertEqual(Booking.objects.filter(member=member).count(), 2)
