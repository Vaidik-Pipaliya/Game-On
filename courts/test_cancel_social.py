from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase

from .models import Booking, SocialSession
from .services import (
    DailyLimitReached, InvalidSlot, SessionFull, SlotTaken, book_court, cancel_booking,
    create_social_session, grid_for_day, join_social_session, seats_taken,
)
from .tests import NOW, Fixtures, ParallelMixin, at

FRIDAY, SATURDAY = 9, 10  # October 2026


class CancelTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()
        self.member = self.make_member(self.silver)

    def test_cancel_more_than_24h_ahead_refunds_in_full(self):
        booking = self.book(self.court1, at(10, 18), member=self.member)
        result = cancel_booking(booking, now=at(8, 17))  # 49h before
        self.assertEqual(result.refund_paise, 68000)
        self.assertEqual(result.booking.status, Booking.Status.CANCELLED)

    def test_cancel_inside_24h_gives_no_refund(self):
        booking = self.book(self.court1, at(10, 18), member=self.member)
        self.assertEqual(cancel_booking(booking, now=at(10, 8)).refund_paise, 0)

    def test_exactly_24h_ahead_still_refunds(self):
        booking = self.book(self.court1, at(10, 18), member=self.member)
        self.assertEqual(cancel_booking(booking, now=at(9, 18)).refund_paise, 68000)

    def test_cancelling_frees_the_slot_for_someone_else(self):
        booking = self.book(self.court1, at(10, 18), guest_name="A", guest_phone="1")
        cancel_booking(booking, now=NOW)
        self.book(self.court1, at(10, 18), guest_name="B", guest_phone="2")  # no SlotTaken

    def test_cancelling_twice_is_rejected(self):
        booking = self.book(self.court1, at(10, 18), guest_name="A", guest_phone="1")
        cancel_booking(booking, now=NOW)
        with self.assertRaises(ValidationError):
            cancel_booking(booking, now=NOW)


class SocialSessionTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()

    def make_session(self, capacity=3, price=20000, day=FRIDAY, hour=19):
        return create_social_session(
            court=self.court1, start=at(day, hour), capacity=capacity, price_per_player_paise=price, now=NOW
        )

    def test_session_must_be_on_a_friday(self):
        with self.assertRaises(InvalidSlot):
            self.make_session(day=SATURDAY)

    def test_session_blocks_whole_court_booking_on_that_slot(self):
        self.make_session()
        with self.assertRaises(SlotTaken):
            self.book(self.court1, at(FRIDAY, 19), guest_name="G", guest_phone="1")
        with self.assertRaises(SlotTaken):
            self.book(self.court1, at(FRIDAY, 19, 30), guest_name="G", guest_phone="1")

    def test_other_courts_and_other_hours_stay_bookable(self):
        self.make_session()
        self.book(self.court2, at(FRIDAY, 19), guest_name="G", guest_phone="1")
        self.book(self.court1, at(FRIDAY, 20), guest_name="G", guest_phone="1")

    def test_cannot_create_session_over_an_existing_booking(self):
        self.book(self.court1, at(FRIDAY, 19), guest_name="G", guest_phone="1")
        with self.assertRaises(SlotTaken):
            self.make_session()
        self.assertEqual(SocialSession.objects.count(), 0)  # rolled back together

    def test_players_join_until_capacity_then_session_is_full(self):
        session = self.make_session(capacity=3)
        for i in range(3):
            join_social_session(session=session, guest_name=f"P{i}", guest_phone="1", now=NOW)
        with self.assertRaises(SessionFull):
            join_social_session(session=session, guest_name="Late", guest_phone="1", now=NOW)
        self.assertEqual(seats_taken(session), 3)

    def test_cancelled_seat_frees_a_place(self):
        session = self.make_session(capacity=2)
        seat = join_social_session(session=session, guest_name="A", guest_phone="1", now=NOW)
        join_social_session(session=session, guest_name="B", guest_phone="2", now=NOW)
        with self.assertRaises(SessionFull):
            join_social_session(session=session, guest_name="C", guest_phone="3", now=NOW)
        cancel_booking(seat, now=NOW)
        join_social_session(session=session, guest_name="C", guest_phone="3", now=NOW)  # place is free again

    def test_member_cannot_join_the_same_session_twice(self):
        session, member = self.make_session(), self.make_member(self.silver)
        join_social_session(session=session, member=member, now=NOW)
        with self.assertRaisesMessage(ValidationError, "already joined"):
            join_social_session(session=session, member=member, now=NOW)

    def test_social_seat_counts_toward_daily_limit(self):
        session, member = self.make_session(), self.make_member(self.silver)
        self.book(self.court2, at(FRIDAY, 8), member=member)
        self.book(self.court2, at(FRIDAY, 10), member=member)
        with self.assertRaises(DailyLimitReached):
            join_social_session(session=session, member=member, now=NOW)

    def test_member_discount_applies_to_social_price(self):
        session = self.make_session(price=20000)
        guest = join_social_session(session=session, guest_name="G", guest_phone="1", now=NOW)
        silver = join_social_session(session=session, member=self.make_member(self.silver), now=NOW)
        self.assertEqual((guest.price_paise, silver.price_paise), (20000, 17000))

    def test_social_seat_does_not_use_up_a_free_court_hour(self):
        session = self.make_session(price=0)
        gold = self.make_member(self.gold)
        join_social_session(session=session, member=gold, now=NOW)
        self.assertEqual(self.book(self.court2, at(FRIDAY, 8), member=gold).price_paise, 0)
        self.assertEqual(self.book(self.court2, at(SATURDAY, 8), member=gold).price_paise, 0)  # 2 free hours intact

    def test_cannot_cancel_the_session_block_as_a_normal_booking(self):
        session = self.make_session()
        block = Booking.objects.get(social_session=session, kind=Booking.Kind.EXCLUSIVE)
        with self.assertRaises(ValidationError):
            cancel_booking(block, now=NOW)


class GridTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()

    def cell(self, rows, court, start):
        row = next(r for r in rows if r["court"] == court)
        return next(c for c in row["cells"] if c["start"] == start)

    def test_grid_marks_free_taken_social_and_past(self):
        self.book(self.court1, at(FRIDAY, 18), guest_name="G", guest_phone="1")
        create_social_session(court=self.court2, start=at(FRIDAY, 19), capacity=4, price_per_player_paise=0, now=NOW)
        _, rows = grid_for_day(at(FRIDAY, 0).date(), now=at(FRIDAY, 7))
        self.assertEqual(self.cell(rows, self.court1, at(FRIDAY, 18))["state"], "taken")
        self.assertEqual(self.cell(rows, self.court1, at(FRIDAY, 18, 30))["state"], "taken")  # overlaps the hour
        self.assertEqual(self.cell(rows, self.court1, at(FRIDAY, 19))["state"], "free")
        self.assertEqual(self.cell(rows, self.court2, at(FRIDAY, 19))["state"], "social")
        self.assertEqual(self.cell(rows, self.court1, at(FRIDAY, 6))["state"], "past")

    def test_cancelled_booking_shows_free_again(self):
        booking = self.book(self.court1, at(FRIDAY, 18), guest_name="G", guest_phone="1")
        cancel_booking(booking, now=NOW)
        _, rows = grid_for_day(at(FRIDAY, 0).date(), now=NOW)
        self.assertEqual(self.cell(rows, self.court1, at(FRIDAY, 18))["state"], "free")

    def test_grid_has_31_slots_from_0600_to_2100(self):
        starts, _ = grid_for_day(at(FRIDAY, 0).date(), now=NOW)
        self.assertEqual((len(starts), starts[0], starts[-1]), (31, at(FRIDAY, 6), at(FRIDAY, 21)))  # a 21:30 start would end after closing


class SocialConcurrencyTests(ParallelMixin, Fixtures, TransactionTestCase):
    def setUp(self):
        self.make_world()

    def test_twenty_simultaneous_joins_fill_exactly_twelve_places(self):
        session = create_social_session(
            court=self.court1, start=at(FRIDAY, 19), capacity=12, price_per_player_paise=0, now=NOW
        )
        results = self.run_parallel(
            [lambda i=i: join_social_session(session=session, guest_name=f"P{i}", guest_phone="1", now=NOW) for i in range(20)]
        )
        self.assertEqual(results.count("ok"), 12)
        self.assertEqual(results.count("SessionFull"), 8)
        self.assertEqual(seats_taken(session), 12)
