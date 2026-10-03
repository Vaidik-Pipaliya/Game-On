"""Court booking rules. The database constraint is the last line of defence; this layer gives friendly errors."""

from collections import namedtuple
from contextlib import contextmanager
from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.audit import record as audit
from config.clock import local_day_bounds
from finance.models import Method, Source
from finance.services import DESK_METHODS, record_payment, record_refund
from members.models import Member
from members.pricing import price_for
from notifications.services import notify_booking_after_commit

from .models import Booking, Court, SocialSession

SESSION = timedelta(hours=1)
OPEN_HOUR, CLOSE_HOUR = 6, 22  # club-local opening hours (assumed; a setting later)
DEFAULT_DAILY_LIMIT = 2  # for members whose plan limit no longer applies (expired)
CANCEL_WINDOW = timedelta(hours=24)  # full refund if cancelled at least this long before the session
FRIDAY = 4
OVERLAP_CONSTRAINT = "no_overlapping_court_bookings"

CancelResult = namedtuple("CancelResult", "booking refund_paise")


class InvalidSlot(ValidationError):
    pass


class SlotTaken(ValidationError):
    pass


class DailyLimitReached(ValidationError):
    pass


class SessionFull(ValidationError):
    pass


def validate_slot_start(start, now):
    if timezone.is_naive(start):
        raise InvalidSlot("The start time must include a timezone.")
    local = timezone.localtime(start)
    if local.minute not in (0, 30) or local.second or local.microsecond:
        raise InvalidSlot("Sessions start on the hour or the half hour. Pick a time like 18:00 or 18:30.")
    opens = local.replace(hour=OPEN_HOUR, minute=0)
    closes = local.replace(hour=CLOSE_HOUR, minute=0)
    if local < opens or local + SESSION > closes:
        raise InvalidSlot(f"Courts are open {OPEN_HOUR:02d}:00 to {CLOSE_HOUR:02d}:00. Pick a session inside those hours.")
    if start <= now:
        raise InvalidSlot("That session has already started. Pick a later time.")


@contextmanager
def _translate_overlap(court, start):
    """Turn the database's overlap error into a friendly SlotTaken."""
    try:
        yield
    except IntegrityError as error:
        if OVERLAP_CONSTRAINT not in str(error):
            raise
        local = timezone.localtime(start)
        raise SlotTaken(f"{court.name} is taken at {local:%H:%M}. Pick another court or time.") from error


def _require_who(member, guest_name, guest_phone):
    if member is None and not (guest_name.strip() and guest_phone.strip()):
        raise ValidationError("Enter the guest's name and phone number.")


def _apply_payment(booking, payment_method):
    """Runs inside the booking's transaction: the booking and its ledger row are saved together or not at all."""
    if booking.price_paise == 0:
        booking.is_paid = True
    elif payment_method in DESK_METHODS:
        record_payment(
            source=Source.COURT, method=payment_method, amount_paise=booking.price_paise,
            reference_id=booking.pk, note=f"Court booking #{booking.pk}",
        )
        booking.is_paid = True
        booking.payment_method = payment_method
    elif payment_method == Method.ONLINE:
        booking.payment_method = Method.ONLINE  # marked paid when Razorpay confirms (finance.mark_payment_captured)
    booking.save(update_fields=["is_paid", "payment_method"])


def take_booking_payment(booking, payment_method):
    """Pay for an unpaid booking later (e.g. an online payment that was abandoned)."""
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)  # no double payment on double-click
        if booking.status != Booking.Status.CONFIRMED:
            raise ValidationError("This booking is cancelled.")
        if booking.is_paid:
            raise ValidationError("This booking is already paid.")
        _apply_payment(booking, payment_method)
    return booking


def book_court(*, court, start, member=None, guest_name="", guest_phone="", payment_method=None,
               created_by=None, now=None):
    """Book one 60-minute session. Returns the confirmed Booking or raises a ValidationError subclass.

    payment_method: cash/card/upi records the payment now; "online" leaves it unpaid until
    Razorpay confirms; None means pay later.
    """
    now = now or timezone.now()
    validate_slot_start(start, now)
    if not court.is_active:
        raise ValidationError(f"{court.name} is not open for booking.")
    _require_who(member, guest_name, guest_phone)

    day = timezone.localtime(start).date()
    with _translate_overlap(court, start), transaction.atomic():
        membership = None
        price_paise = court.walk_in_rate_paise
        if member is not None:
            # Lock this member's row: a second request for the same member waits here until the
            # first commits, so both cannot read "1 booking today" and both succeed.
            member = Member.objects.select_for_update().get(pk=member.pk)
            membership = member.current_membership
            _enforce_daily_limit(member, membership, day)
            price_paise = price_for(
                "court", court.walk_in_rate_paise, membership,
                free_hours_left=_free_hours_left(member, membership, day), today=day,
            ).amount_paise
        # An overlapping insert raises IntegrityError from the exclusion constraint.
        booking = Booking.objects.create(
            court=court, member=member, guest_name=guest_name.strip(), guest_phone=guest_phone.strip(),
            start=start, end=start + SESSION, price_paise=price_paise, created_by=created_by,
        )
        _apply_payment(booking, payment_method)
        notify_booking_after_commit(booking, "booking_confirmed")
        return booking


def _enforce_daily_limit(member, membership, day):
    on_plan = membership is not None and membership.status_on(day) in ("active", "expiring")
    limit = membership.plan.daily_booking_limit if on_plan else DEFAULT_DAILY_LIMIT
    day_start, day_end = local_day_bounds(day)
    # Only confirmed bookings count (whole-court and social seats), so a cancelled one gives the quota back.
    booked = Booking.objects.filter(
        member=member, status=Booking.Status.CONFIRMED, start__gte=day_start, start__lt=day_end
    ).count()
    if booked >= limit:
        raise DailyLimitReached(f"{member.full_name} has reached the limit of {limit} bookings on {day:%d %b}.")


def _free_hours_left(member, membership, day):
    if membership is None:
        return 0
    first_of_month = day.replace(day=1)
    first_of_next_month = (first_of_month + timedelta(days=32)).replace(day=1)
    month_start = local_day_bounds(first_of_month)[0]
    month_end = local_day_bounds(first_of_next_month)[0]
    # A free session is one stored at price 0. Cancelling it removes it from this count.
    used = Booking.objects.filter(
        member=member, kind=Booking.Kind.EXCLUSIVE, status=Booking.Status.CONFIRMED, price_paise=0,
        start__gte=month_start, start__lt=month_end,
    ).count()
    return max(0, membership.plan.free_court_hours_per_month - used)


def cancel_booking(booking, *, now=None, by=None):
    """Cancel a confirmed booking. A paid booking cancelled 24h+ ahead is refunded in full, by the same method."""
    now = now or timezone.now()
    with transaction.atomic():
        # Lock the row so a double-click cancels once and refunds once.
        booking = Booking.objects.select_for_update().get(pk=booking.pk)
        if booking.status != Booking.Status.CONFIRMED:
            raise ValidationError("This booking is already cancelled.")
        if booking.kind == Booking.Kind.EXCLUSIVE and booking.social_session_id:
            raise ValidationError("This slot belongs to a social session. Cancel the session instead.")
        booking.status = Booking.Status.CANCELLED
        booking.save(update_fields=["status"])
        refund = booking.price_paise if booking.is_paid and booking.start - now >= CANCEL_WINDOW else 0
        if refund:
            record_refund(
                source=Source.COURT, method=booking.payment_method, amount_paise=refund,
                reference_id=booking.pk, note=f"Cancelled booking #{booking.pk}",
            )
        audit(by, "booking.cancel", booking, f"Cancelled booking #{booking.pk} ({booking.court.name} {booking.start:%d %b %H:%M})",
              refund_paise=refund, price_paise=booking.price_paise, method=booking.payment_method)
        notify_booking_after_commit(booking, "booking_cancelled")
    return CancelResult(booking, refund)


def create_social_session(*, court, start, capacity, price_per_player_paise, created_by=None, now=None):
    """Open a Friday social session on a court.

    It also creates an ordinary whole-court booking for the same hour. That row is what stops
    anyone booking the court exclusively, using the same database constraint as everything else.
    """
    now = now or timezone.now()
    validate_slot_start(start, now)
    if timezone.localtime(start).weekday() != FRIDAY:
        raise InvalidSlot("Social play runs on Fridays. Pick a Friday.")
    if capacity < 2:
        raise ValidationError("A social session needs room for at least 2 players.")
    with _translate_overlap(court, start), transaction.atomic():
        session = SocialSession.objects.create(
            court=court, start=start, end=start + SESSION, capacity=capacity,
            price_per_player_paise=price_per_player_paise,
        )
        Booking.objects.create(
            court=court, start=start, end=start + SESSION, social_session=session,
            guest_name="Friday social play", created_by=created_by,
        )
    return session


def seats_taken(session):
    return Booking.objects.filter(
        social_session=session, kind=Booking.Kind.SOCIAL, status=Booking.Status.CONFIRMED
    ).count()


def join_social_session(*, session, member=None, guest_name="", guest_phone="", payment_method=None,
                        created_by=None, now=None):
    """Add one player. Counts toward a member's daily limit like any other booking."""
    now = now or timezone.now()
    if session.start <= now:
        raise ValidationError("This session has already started.")
    _require_who(member, guest_name, guest_phone)

    day = timezone.localtime(session.start).date()
    with transaction.atomic():
        membership = None
        if member is not None:
            member = Member.objects.select_for_update().get(pk=member.pk)
            membership = member.current_membership
            _enforce_daily_limit(member, membership, day)
        # Lock the session row: concurrent joins take turns, so the count below is never stale.
        session = SocialSession.objects.select_for_update().get(pk=session.pk)
        taken = seats_taken(session)
        if taken >= session.capacity:
            raise SessionFull(f"This session is full ({taken} of {session.capacity} places taken).")
        if member is not None and Booking.objects.filter(
            social_session=session, member=member, kind=Booking.Kind.SOCIAL, status=Booking.Status.CONFIRMED
        ).exists():
            raise ValidationError(f"{member.full_name} has already joined this session.")
        price = price_for("court", session.price_per_player_paise, membership, today=day).amount_paise
        seat = Booking.objects.create(
            court=session.court, member=member, guest_name=guest_name.strip(), guest_phone=guest_phone.strip(),
            start=session.start, end=session.end, kind=Booking.Kind.SOCIAL, social_session=session,
            price_paise=price, created_by=created_by,
        )
        _apply_payment(seat, payment_method)
        notify_booking_after_commit(seat, "booking_confirmed")
        return seat


def grid_for_day(day, sport=None, now=None):
    """Data for the court grid: (slot start times, one row per court with one cell per start time).

    A cell is "free" (a session can start here), "taken" (a booking overlaps it),
    "social" (a social session overlaps it) or "past".
    """
    now = now or timezone.now()
    tz = timezone.get_current_timezone()
    first = datetime.combine(day, time(OPEN_HOUR), tzinfo=tz)
    starts = [first + timedelta(minutes=30 * i) for i in range((CLOSE_HOUR - OPEN_HOUR) * 2 - 1)]

    courts = Court.objects.filter(is_active=True).select_related("sport").order_by("sport__name", "name")
    if sport is not None:
        courts = courts.filter(sport=sport)
    day_start, day_end = local_day_bounds(day)
    bookings = Booking.objects.filter(
        court__in=courts, kind=Booking.Kind.EXCLUSIVE, status=Booking.Status.CONFIRMED,
        start__lt=day_end, end__gt=day_start,
    ).select_related("member")

    rows = []
    for court in courts:
        mine = [b for b in bookings if b.court_id == court.id]
        cells = []
        for slot in starts:
            booking = next((b for b in mine if b.start < slot + SESSION and b.end > slot), None)
            if booking:
                state = "social" if booking.social_session_id else "taken"
            else:
                state = "past" if slot <= now else "free"
            cells.append({"start": slot, "booking": booking, "state": state})
        rows.append({"court": court, "cells": cells})
    return starts, rows


def day_bookings(day):
    """Everything booked on a club-local day (whole-court and social seats), for the list under the grid."""
    day_start, day_end = local_day_bounds(day)
    return (
        Booking.objects.filter(start__gte=day_start, start__lt=day_end)
        .exclude(kind=Booking.Kind.EXCLUSIVE, social_session__isnull=False)  # hide the court-block row of a social session
        .select_related("court", "member")
        .order_by("start", "court__name")
    )
