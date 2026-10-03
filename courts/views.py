from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import role_required
from accounts.templatetags.money import rupees
from finance.models import Method, Source
from finance.services import OnlinePaymentUnavailable, start_online_payment

from .forms import BookingForm, SocialCreateForm, SocialJoinForm
from .models import Booking, Court, SocialSession, Sport
from .services import (
    book_court, cancel_booking, create_social_session, day_bookings, grid_for_day,
    join_social_session, seats_taken, take_booking_payment,
)

desk_only = role_required("owner", "front_desk")
owner_only = role_required("owner")


def _parse_day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return timezone.localdate()


def _first_error(form):
    return next(iter(form.errors.values()))[0]


def _grid_url(booking):
    return f"/desk/book/?date={timezone.localtime(booking.start).date().isoformat()}"


def _pay_online(request, booking):
    """Create a Razorpay order and open Checkout. If Razorpay is unavailable, the booking simply stays unpaid."""
    try:
        payment = start_online_payment(source=Source.COURT, reference_id=booking.pk, amount_paise=booking.price_paise)
    except OnlinePaymentUnavailable as error:
        messages.error(request, f"{error} The booking is saved as unpaid.")
        return redirect(_grid_url(booking))
    return redirect("pay_page", pk=payment.pk)


@desk_only
def booking_grid(request):
    day = _parse_day(request.GET.get("date"))
    sport = Sport.objects.filter(pk=request.GET.get("sport") or None).first()
    starts, rows = grid_for_day(day, sport)
    return render(request, "courts/grid.html", {
        "day": day, "prev_day": day - timedelta(days=1), "next_day": day + timedelta(days=1),
        "sports": Sport.objects.order_by("name"), "sport": sport,
        "starts": starts, "rows": rows, "bookings": day_bookings(day), "methods": Method.choices,
    })


@desk_only
def booking_new(request):
    form = BookingForm(request.POST or None, initial={
        "court": request.GET.get("court"), "start": request.GET.get("start"),
    })
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            booking = book_court(
                court=data["court"], start=data["start"], member=data["member"],
                guest_name=data["guest_name"], guest_phone=data["guest_phone"],
                payment_method=data["payment_method"], created_by=request.user,
            )
        except ValidationError as error:  # slot taken, daily limit, outside hours...
            form.add_error(None, error.messages)
        else:
            who = booking.member.full_name if booking.member else booking.guest_name
            messages.success(request, f"Booked {booking.court.name} at {timezone.localtime(booking.start):%H:%M} for {who}: {rupees(booking.price_paise)}.")
            if not booking.is_paid and booking.payment_method == Method.ONLINE:
                return _pay_online(request, booking)
            return redirect(_grid_url(booking))
    court = Court.objects.filter(pk=form["court"].value() or None).first()
    return render(request, "courts/booking_new.html", {"form": form, "court": court, "start": form["start"].value()})


@desk_only
@require_POST
def booking_cancel(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    try:
        result = cancel_booking(booking)
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        if result.refund_paise:
            messages.success(request, f"Booking cancelled. Refund {rupees(result.refund_paise)} by {booking.payment_method}.")
        elif booking.is_paid and booking.price_paise:
            messages.success(request, "Booking cancelled. No refund: it is inside the 24-hour window.")
        else:
            messages.success(request, "Booking cancelled.")
    return redirect(_grid_url(booking))


@desk_only
@require_POST
def booking_pay(request, pk):
    """Take payment for an unpaid booking: at the desk, or online via Razorpay."""
    booking = get_object_or_404(Booking, pk=pk)
    method = request.POST.get("payment_method")
    if method not in Method.values:
        messages.error(request, "Choose how the booking was paid.")
        return redirect(_grid_url(booking))
    if method == Method.ONLINE:
        if booking.is_paid or booking.status != Booking.Status.CONFIRMED:
            messages.error(request, "This booking does not need a payment.")
            return redirect(_grid_url(booking))
        return _pay_online(request, booking)
    try:
        take_booking_payment(booking, method)
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        messages.success(request, f"Payment of {rupees(booking.price_paise)} recorded ({method}).")
    return redirect(_grid_url(booking))


@desk_only
def social_list(request):
    sessions = list(SocialSession.objects.filter(start__gt=timezone.now()).select_related("court").order_by("start"))
    for session in sessions:
        session.taken = seats_taken(session)
        session.players = Booking.objects.filter(
            social_session=session, kind=Booking.Kind.SOCIAL, status=Booking.Status.CONFIRMED
        ).select_related("member")
    can_create = request.user.is_superuser or request.user.role == "owner"
    return render(request, "courts/social.html", {
        "sessions": sessions, "create_form": SocialCreateForm() if can_create else None,
    })


@owner_only
@require_POST
def social_create(request):
    form = SocialCreateForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return redirect("social_list")
    data = form.cleaned_data
    try:
        create_social_session(
            court=data["court"], start=data["start"], capacity=data["capacity"],
            price_per_player_paise=data["price_rupees"] * 100, created_by=request.user,
        )
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        messages.success(request, "Social session created. The court is blocked for whole-court bookings.")
    return redirect("social_list")


@desk_only
@require_POST
def social_join(request, pk):
    session = get_object_or_404(SocialSession, pk=pk)
    form = SocialJoinForm(request.POST)
    if not form.is_valid():
        messages.error(request, _first_error(form))
        return redirect("social_list")
    data = form.cleaned_data
    try:
        join_social_session(
            session=session, member=data["member"], guest_name=data["guest_name"],
            guest_phone=data["guest_phone"], payment_method=data["payment_method"], created_by=request.user,
        )
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        messages.success(request, "Player added.")
    return redirect("social_list")
