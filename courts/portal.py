"""Member self-service: members book, pay and cancel their own courts. Staff screens live in views.py."""

from datetime import date, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.templatetags.money import rupees
from finance.models import Source
from finance.services import OnlinePaymentUnavailable, start_online_payment

from .forms import parse_local_datetime
from .models import Booking, Court, Sport
from .services import HOLD_FOR, book_court, cancel_booking, court_price, grid_for_day, release_hold

BOOKING_WINDOW_DAYS = 14  # how far ahead members can book


def member_required(view):
    """Logged-in users whose account is linked to a Member record (done at the desk, by email)."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        member = getattr(request.user, "member", None)
        if member is None:
            return render(request, "courts/portal_no_member.html", status=403)
        return view(request, member, *args, **kwargs)

    return wrapper


def _day(value):
    today = timezone.localdate()
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError):
        return today
    return min(max(day, today), today + timedelta(days=BOOKING_WINDOW_DAYS))


def _start_online_payment(request, booking):
    """Create the Razorpay order for a held (or unpaid) booking and open Checkout."""
    try:
        payment = start_online_payment(source=Source.COURT, reference_id=booking.pk, amount_paise=booking.price_paise)
    except OnlinePaymentUnavailable as error:
        if booking.status == Booking.Status.HELD:
            release_hold(booking)  # don't keep the slot blocked for a payment that can't start
        messages.error(request, f"{error} Choose 'Pay at the club' instead.")
        return redirect("portal_grid")
    return redirect("pay_page", pk=payment.pk)


@member_required
def grid(request, member):
    day = _day(request.GET.get("date"))
    sport = Sport.objects.filter(pk=request.GET.get("sport") or None).first()
    starts, rows = grid_for_day(day, sport)
    today = timezone.localdate()
    return render(request, "courts/portal_grid.html", {
        "member": member, "day": day, "starts": starts, "rows": rows, "sports": Sport.objects.order_by("name"), "sport": sport,
        "prev_day": day - timedelta(days=1) if day > today else None,
        "next_day": day + timedelta(days=1) if day < today + timedelta(days=BOOKING_WINDOW_DAYS) else None,
    })


@member_required
def confirm(request, member):
    court = get_object_or_404(Court, pk=request.GET.get("court") or request.POST.get("court"), is_active=True)
    raw_start = request.GET.get("start") or request.POST.get("start")
    try:
        start = parse_local_datetime(raw_start)
    except ValidationError:
        messages.error(request, "Pick a time from the grid.")
        return redirect("portal_grid")
    day = timezone.localtime(start).date()
    price = court_price(court, member, day)
    error = None

    if request.method == "POST":
        online = request.POST.get("payment") == "online"
        try:
            booking = book_court(court=court, start=start, member=member, hold=online, created_by=request.user)
        except ValidationError as exc:  # slot taken, daily limit, outside hours...
            error = exc.messages[0]
        else:
            if booking.status == Booking.Status.HELD:
                messages.info(request, f"Your slot is held for {int(HOLD_FOR.total_seconds() // 60)} minutes while you pay.")
                return _start_online_payment(request, booking)
            messages.success(request, f"Booked {court.name} on {timezone.localtime(start):%a %d %b} at {timezone.localtime(start):%I:%M %p}.")
            return redirect("portal_mine")

    return render(request, "courts/portal_confirm.html", {
        "court": court, "start": start, "raw_start": raw_start, "price": price, "error": error,
        "hold_minutes": int(HOLD_FOR.total_seconds() // 60),
    })


@member_required
def mine(request, member):
    now = timezone.now()
    mine_qs = Booking.objects.filter(member=member).select_related("court").order_by("-start")
    upcoming = [b for b in mine_qs if b.start > now and b.status in (Booking.Status.HELD, Booking.Status.CONFIRMED)]
    past = [b for b in mine_qs if b not in upcoming][:15]
    return render(request, "courts/portal_mine.html", {"upcoming": sorted(upcoming, key=lambda b: b.start), "past": past, "now": now})


def _own_booking(member, pk):
    return get_object_or_404(Booking, pk=pk, member=member)  # someone else's booking is simply "not found"


@member_required
@require_POST
def cancel(request, member, pk):
    booking = _own_booking(member, pk)
    if booking.status == Booking.Status.HELD:
        release_hold(booking)
        messages.success(request, "Your held slot was released.")
        return redirect("portal_mine")
    try:
        result = cancel_booking(booking, by=request.user)
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        if result.refund_paise and booking.payment_method == "online":
            messages.success(request, f"Booking cancelled. Your refund of {rupees(result.refund_paise)} is on its way back to how you paid; it can take a few days to show up.")
        elif result.refund_paise:
            messages.success(request, f"Booking cancelled. Your refund of {rupees(result.refund_paise)} is recorded; the club will return it to how you paid.")
        elif booking.is_paid and booking.price_paise:
            messages.success(request, "Booking cancelled. No refund: it is inside the 24-hour window.")
        else:
            messages.success(request, "Booking cancelled.")
    return redirect("portal_mine")


@member_required
@require_POST
def pay(request, member, pk):
    booking = _own_booking(member, pk)
    if booking.is_paid or booking.status not in (Booking.Status.HELD, Booking.Status.CONFIRMED) or not booking.price_paise:
        messages.error(request, "This booking doesn't need a payment.")
        return redirect("portal_mine")
    return _start_online_payment(request, booking)
