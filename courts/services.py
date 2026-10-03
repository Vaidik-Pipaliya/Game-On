"""Court booking rules. The database constraint is the last line of defence; this layer gives friendly errors."""

from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from members.models import Member
from members.pricing import price_for

from .models import Booking

SESSION = timedelta(hours=1)
OPEN_HOUR, CLOSE_HOUR = 6, 22  # club-local opening hours (assumed; a setting later)
DEFAULT_DAILY_LIMIT = 2  # for walk-in-rate members whose plan limit no longer applies
OVERLAP_CONSTRAINT = "no_overlapping_court_bookings"


class InvalidSlot(ValidationError):
    pass


class SlotTaken(ValidationError):
    pass


class DailyLimitReached(ValidationError):
    pass


def _local_bounds(day):
    """[start, end) of one club-local calendar day, as aware datetimes."""
    tz = timezone.get_current_timezone()
    return (
        datetime.combine(day, time.min, tzinfo=tz),
        datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz),
    )


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


def book_court(*, court, start, member=None, guest_name="", guest_phone="", created_by=None, now=None):
    """Book one 60-minute session. Returns the confirmed Booking or raises a ValidationError subclass."""
    now = now or timezone.now()
    validate_slot_start(start, now)
    if not court.is_active:
        raise ValidationError(f"{court.name} is not open for booking.")
    if member is None and not (guest_name.strip() and guest_phone.strip()):
        raise ValidationError("Enter the guest's name and phone number.")

    local_start = timezone.localtime(start)
    day = local_start.date()
    try:
        with transaction.atomic():
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
            return Booking.objects.create(
                court=court, member=member, guest_name=guest_name.strip(), guest_phone=guest_phone.strip(),
                start=start, end=start + SESSION, price_paise=price_paise, created_by=created_by,
            )
    except IntegrityError as error:
        if OVERLAP_CONSTRAINT not in str(error):
            raise
        raise SlotTaken(f"{court.name} is taken at {local_start:%H:%M}. Pick another court or time.") from error


def _enforce_daily_limit(member, membership, day):
    on_plan = membership is not None and membership.status_on(day) in ("active", "expiring")
    limit = membership.plan.daily_booking_limit if on_plan else DEFAULT_DAILY_LIMIT
    day_start, day_end = _local_bounds(day)
    # Only confirmed bookings count, so a cancelled one gives the quota back.
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
    month_start = _local_bounds(first_of_month)[0]
    month_end = _local_bounds(first_of_next_month)[0]
    # A free session is one stored at price 0. Cancelling it removes it from this count.
    used = Booking.objects.filter(
        member=member, status=Booking.Status.CONFIRMED, price_paise=0, start__gte=month_start, start__lt=month_end
    ).count()
    return max(0, membership.plan.free_court_hours_per_month - used)
