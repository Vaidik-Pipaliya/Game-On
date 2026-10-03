from datetime import timedelta

from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from courts.models import Booking, WaitlistEntry
from courts.services import (
    OFFER_HOLD_FOR, SlotTaken, book_court, cancel_booking, confirm_held_booking, join_waitlist, leave_waitlist,
    offer_to_waitlist, release_expired_holds, release_hold,
)

from .test_views import future_day, local
from .tests import NOW, Fixtures, at

LATER = NOW + OFFER_HOLD_FOR + timedelta(minutes=1)  # past a 30-minute offer


class WaitlistCase(Fixtures, TestCase):
    def setUp(self):
        self.make_world()
        self.owner = self.make_member(self.silver, name="Slot Owner")
        self.first = self.make_member(self.silver, name="First In Line")
        self.second = self.make_member(self.silver, name="Second In Line")
        self.first.email, self.second.email = "first@example.com", "second@example.com"
        self.first.save()
        self.second.save()
        self.booking = self.book(self.court1, at(10, 18), member=self.owner)

    def wait(self, member):
        return join_waitlist(member, self.court1, at(10, 18), now=NOW)

    def cancel_owner_booking(self, now=NOW):
        with self.captureOnCommitCallbacks(execute=True):
            cancel_booking(self.booking, now=now)


class JoinTests(WaitlistCase):
    def test_can_only_wait_for_a_slot_that_is_actually_taken(self):
        with self.assertRaisesMessage(Exception, "free right now"):
            join_waitlist(self.first, self.court1, at(10, 19), now=NOW)

    def test_overlapping_half_hour_counts_as_taken(self):
        self.assertEqual(join_waitlist(self.first, self.court1, at(10, 18, 30), now=NOW).status, "waiting")

    def test_joining_twice_is_refused_by_the_database_too(self):
        self.wait(self.first)
        with self.assertRaisesMessage(Exception, "already on the waitlist"):
            self.wait(self.first)
        with self.assertRaises(IntegrityError), transaction.atomic():
            WaitlistEntry.objects.create(member=self.first, court=self.court1, start=at(10, 18))

    def test_a_member_can_wait_for_at_most_five_slots(self):
        for hour in range(7, 12):
            book_court(court=self.court2, start=at(10, hour), guest_name="G", guest_phone="1", now=NOW)
            join_waitlist(self.first, self.court2, at(10, hour), now=NOW)
        book_court(court=self.court2, start=at(10, 13), guest_name="G", guest_phone="1", now=NOW)
        with self.assertRaisesMessage(Exception, "up to 5 slots"):
            join_waitlist(self.first, self.court2, at(10, 13), now=NOW)


class OfferTests(WaitlistCase):
    def test_cancelling_offers_the_slot_to_the_first_in_line_who_gets_a_hold_and_an_email(self):
        first_entry = self.wait(self.first)
        self.wait(self.second)
        self.cancel_owner_booking()
        first_entry.refresh_from_db()
        self.assertEqual(first_entry.status, "offered")
        self.assertEqual((first_entry.booking.member, first_entry.booking.status), (self.first, "held"))
        self.assertEqual(first_entry.booking.hold_expires_at, first_entry.offered_at + OFFER_HOLD_FOR)
        self.assertEqual(WaitlistEntry.objects.get(member=self.second).status, "waiting")
        self.assertEqual([m.to for m in mail.outbox if "slot opened" in m.subject], [["first@example.com"]])

    def test_the_offered_slot_is_protected_nobody_else_can_book_it(self):
        self.wait(self.first)
        self.cancel_owner_booking()
        with self.assertRaises(SlotTaken):
            self.book(self.court1, at(10, 18), guest_name="Sneaky", guest_phone="9")

    def test_confirming_the_offer_books_the_slot_and_closes_the_entry(self):
        entry = self.wait(self.first)
        self.cancel_owner_booking()
        entry.refresh_from_db()
        booking = confirm_held_booking(entry.booking, self.first, now=NOW)
        entry.refresh_from_db()
        self.assertEqual((booking.status, booking.is_paid, booking.hold_expires_at, entry.status), ("confirmed", False, None, "done"))

    def test_an_expired_offer_passes_to_the_next_person(self):
        first_entry, second_entry = self.wait(self.first), self.wait(self.second)
        self.cancel_owner_booking()
        release_expired_holds(LATER)  # first person did not confirm in time
        first_entry.refresh_from_db()
        second_entry.refresh_from_db()
        self.assertEqual((first_entry.status, second_entry.status), ("expired", "offered"))
        self.assertEqual(second_entry.booking.status, "held")

    def test_declining_an_offer_passes_it_on_immediately(self):
        first_entry, second_entry = self.wait(self.first), self.wait(self.second)
        self.cancel_owner_booking()
        first_entry.refresh_from_db()
        release_hold(first_entry.booking, now=NOW)
        second_entry.refresh_from_db()
        self.assertEqual((WaitlistEntry.objects.get(pk=first_entry.pk).status, second_entry.status), ("expired", "offered"))

    def test_leaving_while_offered_releases_the_slot_to_the_next_person(self):
        first_entry, second_entry = self.wait(self.first), self.wait(self.second)
        self.cancel_owner_booking()
        first_entry.refresh_from_db()
        self.assertTrue(leave_waitlist(first_entry, self.first, now=NOW))
        second_entry.refresh_from_db()
        self.assertEqual((WaitlistEntry.objects.get(pk=first_entry.pk).status, second_entry.status), ("left", "offered"))

    def test_someone_who_cannot_take_the_slot_is_skipped(self):
        first_entry, second_entry = self.wait(self.first), self.wait(self.second)
        for hour in (8, 9):  # the first person is now at their daily limit
            book_court(court=self.court2, start=at(10, hour), member=self.first, now=NOW)
        self.cancel_owner_booking()
        first_entry.refresh_from_db()
        second_entry.refresh_from_db()
        self.assertEqual((first_entry.status, second_entry.status), ("left", "offered"))

    def test_an_empty_queue_does_nothing_and_a_past_slot_is_never_offered(self):
        self.cancel_owner_booking()  # nobody is waiting: nothing happens
        self.assertEqual(Booking.objects.filter(status="held").count(), 0)
        entry = WaitlistEntry.objects.create(member=self.first, court=self.court1, start=at(10, 18))
        self.assertIsNone(offer_to_waitlist(self.court1, at(10, 18), now=at(10, 19)))  # the session already started
        entry.refresh_from_db()
        self.assertEqual(entry.status, "waiting")

    def test_a_free_session_offer_is_still_held_not_booked_behind_the_members_back(self):
        gold = self.make_member(self.gold, name="Gold Gita")
        entry = self.wait(gold)
        self.cancel_owner_booking()
        entry.refresh_from_db()
        self.assertEqual((entry.booking.price_paise, entry.booking.status), (0, "held"))


class WaitlistScreenTests(WaitlistCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="f", email="first@example.com", role="member")
        self.first.user = self.user
        self.first.save()
        self.client.force_login(self.user)
        self.day = future_day(min_days=4)
        self.taken = book_court(court=self.court1, start=local(self.day, 18), guest_name="G", guest_phone="9000000001")

    def join_url(self):
        return f"{reverse('portal_waitlist_join')}?court={self.court1.pk}&start={self.day.isoformat()}T18:00"

    def test_taken_cells_link_to_the_waitlist(self):
        page = self.client.get(reverse("portal_grid"), {"date": self.day.isoformat()})
        self.assertContains(page, "portal_waitlist_join".replace("portal_waitlist_join", "/book/waitlist/join/"))

    def test_join_then_get_offered_then_confirm_from_my_bookings(self):
        self.assertContains(self.client.get(self.join_url()), "This slot is taken")
        self.client.post(reverse("portal_waitlist_join"), {"court": self.court1.pk, "start": f"{self.day.isoformat()}T18:00"})
        self.assertContains(self.client.get(reverse("portal_mine")), "On the waitlist")
        with self.captureOnCommitCallbacks(execute=True):
            cancel_booking(self.taken)  # real clock: the screens that follow use it too
        page = self.client.get(reverse("portal_mine"))
        self.assertContains(page, "Held for you until")
        offer = Booking.objects.get(member=self.first, status="held")
        self.client.post(reverse("portal_confirm_hold", args=[offer.pk]))
        offer.refresh_from_db()
        self.assertEqual(offer.status, "confirmed")

    def test_cannot_join_for_a_free_slot_and_cannot_touch_other_peoples_entries(self):
        free = f"{reverse('portal_waitlist_join')}?court={self.court2.pk}&start={self.day.isoformat()}T18:00"
        response = self.client.post(reverse("portal_waitlist_join"), {"court": self.court2.pk, "start": f"{self.day.isoformat()}T18:00"})
        self.assertContains(response, "free right now")
        other = WaitlistEntry.objects.create(member=self.second, court=self.court1, start=local(self.day, 18))
        self.assertEqual(self.client.post(reverse("portal_waitlist_leave", args=[other.pk])).status_code, 404)
        self.assertEqual(self.client.get(free).status_code, 200)

    def test_leaving_from_the_screen(self):
        entry = WaitlistEntry.objects.create(member=self.first, court=self.court1, start=local(self.day, 18))
        self.client.post(reverse("portal_waitlist_leave", args=[entry.pk]))
        entry.refresh_from_db()
        self.assertEqual(entry.status, "left")
