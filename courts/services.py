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
from finance.services import DESK_METHODS, record_payment, record_refund, request_gateway_refund
from members.models import Member
from members.pricing import price_for
from notifications.services import notify_booking_after_commit, send_logged_email

from .models import Booking, Court, SocialSession, WaitlistEntry

SESSION = timedelta(hours=1)
OPEN_HOUR, CLOSE_HOUR = 6, 22  # club-local opening hours (assumed; a setting later)
DEFAULT_DAILY_LIMIT = 2  # for members whose plan limit no longer applies (expired)
CANCEL_WINDOW = timedelta(hours=24)  # full refund if cancelled at least this long before the session
HOLD_FOR = timedelta(minutes=5)  # how long a slot is reserved while an online payment completes
OFFER_HOLD_FOR = timedelta(minutes=30)  # how long a waitlisted member has to confirm an offered slot
MAX_WAITLIST_PER_MEMBER = 5
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


def court_price(court, member, day):
    """What this member (or walk-in, when member is None) pays for a session on `day`, as a Price."""
    membership = member.current_membership if member is not None else None
    free_left = _free_hours_left(member, membership, day) if member is not None else 0
    return price_for("court", court.walk_in_rate_paise, membership, free_hours_left=free_left, today=day)


def release_expired_holds(now=None):
    """Free slots whose 5-minute hold ran out. Runs whenever availability or a booking is looked at,
    so an expired hold never blocks anyone, and a cron job isn't needed for correctness."""
    now = now or timezone.now()
    expired = list(Booking.objects.filter(status=Booking.Status.HELD, hold_expires_at__lte=now)
                   .values_list("pk", "court_id", "start", "kind"))
    if not expired:
        return 0
    ids = [pk for pk, *_ in expired]
    Booking.objects.filter(pk__in=ids, status=Booking.Status.HELD).update(status=Booking.Status.CANCELLED)
    WaitlistEntry.objects.filter(booking_id__in=ids, status=WaitlistEntry.Status.OFFERED).update(status=WaitlistEntry.Status.EXPIRED)
    for _, court_id, start, kind in expired:
        if kind == Booking.Kind.EXCLUSIVE:
            offer_to_waitlist(Court.objects.get(pk=court_id), start, now)  # next in line gets a turn
    return len(ids)


def book_court(*, court, start, member=None, guest_name="", guest_phone="", payment_method=None,
               hold=False, hold_for=None, created_by=None, now=None):
    """Book one 60-minute session. Returns the Booking or raises a ValidationError subclass.

    payment_method: cash/card/upi records the payment now; "online" leaves it unpaid until
    Razorpay confirms; None means pay later.
    hold=True reserves the slot for 5 minutes (status "held") while an online payment completes;
    it becomes confirmed when the payment arrives (apply_online_payment) or is released if it doesn't.
    hold_for (a timedelta) is used for waitlist offers: always held, even if free, for that long.
    """
    now = now or timezone.now()
    validate_slot_start(start, now)
    release_expired_holds(now)
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
            _enforce_daily_limit(member, member.current_membership, day)
            price_paise = court_price(court, member, day).amount_paise
        # An overlapping insert raises IntegrityError from the exclusion constraint.
        # An ordinary payment hold is skipped for a free session (nothing to pay), but an offer is always held.
        held = bool(hold_for) or (hold and price_paise > 0)
        booking = Booking.objects.create(
            court=court, member=member, guest_name=guest_name.strip(), guest_phone=guest_phone.strip(),
            start=start, end=start + SESSION, price_paise=price_paise, created_by=created_by,
            status=Booking.Status.HELD if held else Booking.Status.CONFIRMED,
            hold_expires_at=now + (hold_for or HOLD_FOR) if held else None,
            payment_method=Method.ONLINE if held else "",
        )
        if held:
            return booking  # confirmed (and the message sent) when the payment arrives
        _apply_payment(booking, payment_method)
        notify_booking_after_commit(booking, "booking_confirmed")
        return booking


def _enforce_daily_limit(member, membership, day):
    on_plan = membership is not None and membership.status_on(day) in ("active", "expiring")
    limit = membership.plan.daily_booking_limit if on_plan else DEFAULT_DAILY_LIMIT
    day_start, day_end = local_day_bounds(day)
    # Held and confirmed bookings count (whole-court and social seats); a cancelled one gives the quota back.
    booked = Booking.objects.filter(
        member=member, status__in=[Booking.Status.HELD, Booking.Status.CONFIRMED],
        start__gte=day_start, start__lt=day_end,
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
            if booking.payment_method == Method.ONLINE:
                request_gateway_refund(source=Source.COURT, reference_id=booking.pk)  # money goes back via Razorpay
        audit(by, "booking.cancel", booking, f"Cancelled booking #{booking.pk} ({booking.court.name} {booking.start:%d %b %H:%M})",
              refund_paise=refund, price_paise=booking.price_paise, method=booking.payment_method)
        notify_booking_after_commit(booking, "booking_cancelled")
        if booking.kind == Booking.Kind.EXCLUSIVE:
            transaction.on_commit(lambda: offer_to_waitlist(booking.court, booking.start, now))
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
    release_expired_holds(now)
    tz = timezone.get_current_timezone()
    first = datetime.combine(day, time(OPEN_HOUR), tzinfo=tz)
    starts = [first + timedelta(minutes=30 * i) for i in range((CLOSE_HOUR - OPEN_HOUR) * 2 - 1)]

    courts = Court.objects.filter(is_active=True).select_related("sport").order_by("sport__name", "name")
    if sport is not None:
        courts = courts.filter(sport=sport)
    day_start, day_end = local_day_bounds(day)
    bookings = Booking.objects.filter(
        court__in=courts, kind=Booking.Kind.EXCLUSIVE, status__in=[Booking.Status.HELD, Booking.Status.CONFIRMED],
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


def release_hold(booking, now=None):
    """The member backed out of paying: free the slot straight away instead of waiting out the 5 minutes."""
    released = Booking.objects.filter(pk=booking.pk, status=Booking.Status.HELD).update(status=Booking.Status.CANCELLED)
    if released:
        WaitlistEntry.objects.filter(booking=booking, status=WaitlistEntry.Status.OFFERED).update(status=WaitlistEntry.Status.EXPIRED)
        if booking.kind == Booking.Kind.EXCLUSIVE:
            offer_to_waitlist(booking.court, booking.start, now)
    return bool(released)


def confirm_held_booking(booking, member, now=None):
    """A held booking (usually a waitlist offer) is confirmed without online payment: pay at the club."""
    now = now or timezone.now()
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk, member=member)
        if booking.status != Booking.Status.HELD or (booking.hold_expires_at and booking.hold_expires_at <= now):
            raise ValidationError("This hold has expired. Join the waitlist again or pick another time.")
        booking.status = Booking.Status.CONFIRMED
        booking.hold_expires_at = None
        booking.is_paid = booking.price_paise == 0
        booking.payment_method = ""
        booking.save(update_fields=["status", "hold_expires_at", "is_paid", "payment_method"])
        WaitlistEntry.objects.filter(booking=booking, status=WaitlistEntry.Status.OFFERED).update(status=WaitlistEntry.Status.DONE)
        notify_booking_after_commit(booking, "booking_confirmed")
    return booking


def apply_online_payment(booking_id, payment):
    """Razorpay confirmed a payment for this booking (finance has already recorded the ledger row).

    - held        -> confirmed and paid
    - confirmed   -> marked paid (staff-created online booking)
    - cancelled   -> the hold had expired: confirm it again if nobody took the slot, otherwise refund
    - already paid-> a second payment for the same booking: refund it
    Returns "confirmed", "paid", "refunded" so callers and tests can see what happened.
    """
    with transaction.atomic():
        booking = Booking.objects.select_for_update().select_related("court").get(pk=booking_id)

        if booking.is_paid:
            return _refund_online(booking, payment, "Duplicate payment for an already paid booking")

        already_confirmed = booking.status == Booking.Status.CONFIRMED  # staff booked it; the customer just paid
        if booking.status == Booking.Status.CANCELLED:
            try:
                with transaction.atomic():  # savepoint: a clash must not poison the outer transaction
                    booking.status = Booking.Status.CONFIRMED
                    booking.save(update_fields=["status"])
            except IntegrityError as error:
                if OVERLAP_CONSTRAINT not in str(error):
                    raise
                booking.status = Booking.Status.CANCELLED
                return _refund_online(booking, payment, "Slot was taken before the payment arrived")
        elif booking.status == Booking.Status.HELD:
            booking.status = Booking.Status.CONFIRMED

        booking.is_paid = True
        booking.payment_method = Method.ONLINE
        booking.hold_expires_at = None
        booking.save(update_fields=["status", "is_paid", "payment_method", "hold_expires_at"])
        WaitlistEntry.objects.filter(booking=booking, status=WaitlistEntry.Status.OFFERED).update(status=WaitlistEntry.Status.DONE)
        if already_confirmed:
            return "paid"  # the confirmation message was sent when staff made the booking
        notify_booking_after_commit(booking, "booking_confirmed")
        return "confirmed"


def _refund_online(booking, payment, reason):
    """Money arrived but the booking can't be honoured: reverse it in the ledger, log why, and have
    Razorpay return the money (after this transaction commits)."""
    record_refund(
        source=Source.COURT, method=Method.ONLINE, amount_paise=payment.amount_paise, reference_id=booking.pk,
        note=f"{reason}. Refund sent back through Razorpay.",
    )
    request_gateway_refund(payment=payment)
    audit(None, "booking.payment_refunded", booking, f"{reason}: booking #{booking.pk}", amount_paise=payment.amount_paise)
    return "refunded"


# ---- Waitlist ----

def join_waitlist(member, court, start, now=None):
    """Queue up for a slot that is currently taken. First come, first served."""
    now = now or timezone.now()
    validate_slot_start(start, now)
    release_expired_holds(now)
    taken = Booking.objects.filter(
        court=court, kind=Booking.Kind.EXCLUSIVE, status__in=[Booking.Status.HELD, Booking.Status.CONFIRMED],
        start__lt=start + SESSION, end__gt=start,
    ).exists()
    if not taken:
        raise ValidationError("That slot is free right now. Book it instead of waiting.")
    live = [WaitlistEntry.Status.WAITING, WaitlistEntry.Status.OFFERED]
    if WaitlistEntry.objects.filter(member=member, status__in=live).count() >= MAX_WAITLIST_PER_MEMBER:
        raise ValidationError(f"You can wait for up to {MAX_WAITLIST_PER_MEMBER} slots at a time. Leave one first.")
    try:
        with transaction.atomic():
            return WaitlistEntry.objects.create(member=member, court=court, start=start)
    except IntegrityError as error:
        if "one_live_waitlist_entry_per_slot" not in str(error):
            raise
        raise ValidationError("You are already on the waitlist for this slot.") from error


def leave_waitlist(entry, member, now=None):
    """Leave the queue. If an offer was already made to you, that held slot is released and passed on."""
    with transaction.atomic():
        # of=("self",): lock only the waitlist row (the joined booking may be NULL, which Postgres can't lock).
        entry = WaitlistEntry.objects.select_for_update(of=("self",)).select_related("booking").get(pk=entry.pk, member=member)
        if entry.status not in (WaitlistEntry.Status.WAITING, WaitlistEntry.Status.OFFERED):
            return False
        offered = entry.booking if entry.status == WaitlistEntry.Status.OFFERED else None
        entry.status = WaitlistEntry.Status.LEFT
        entry.save(update_fields=["status"])
    if offered:
        release_hold(offered, now)  # frees the slot and offers it to the next person
    return True


def offer_to_waitlist(court, start, now=None):
    """A slot just freed up: hold it for the first person waiting and tell them. Returns that entry, or None.

    Someone who can't take it (daily limit reached, membership problem) is skipped and removed from
    the queue. If the slot is already taken again, the queue keeps waiting.
    """
    now = now or timezone.now()
    if start <= now:
        return None
    for entry in WaitlistEntry.objects.filter(court=court, start=start, status=WaitlistEntry.Status.WAITING).select_related("member").order_by("created_at", "pk"):
        try:
            hold = book_court(court=court, start=start, member=entry.member, hold_for=OFFER_HOLD_FOR, now=now)
        except SlotTaken:
            return None
        except ValidationError:
            WaitlistEntry.objects.filter(pk=entry.pk).update(status=WaitlistEntry.Status.LEFT)
            continue
        WaitlistEntry.objects.filter(pk=entry.pk).update(status=WaitlistEntry.Status.OFFERED, booking=hold, offered_at=now)
        entry.refresh_from_db()
        _tell_waitlisted_member(entry, hold)
        return entry
    return None


def _tell_waitlisted_member(entry, hold):
    member = entry.member
    if not member.email:
        return  # they will still see the offer under "My bookings"
    local = timezone.localtime(entry.start)
    minutes = int(OFFER_HOLD_FOR.total_seconds() // 60)
    transaction.on_commit(lambda: send_logged_email(
        to=member.email, member=member, template="waitlist_offer",
        subject=f"A slot opened up: {entry.court.name}, {local:%a %d %b at %I:%M %p}",
        body=(f"Hi {member.full_name},\n\n{entry.court.name} on {local:%a %d %b} at {local:%I:%M %p} is free again and we are "
              f"holding it for you for {minutes} minutes. Open 'My bookings' on the club website to confirm it or pay online.\n"),
    ))
