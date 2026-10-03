"""Bar and cafe rules: tabs, kitchen tickets, automatic member discount, split payments, shifts, day report."""

from collections import namedtuple

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from config.clock import local_day_bounds
from finance.models import Ledger, Method, Source
from finance.services import DESK_METHODS, record_payment
from members.pricing import price_for

from .models import Shift, Tab, TabLine

Bill = namedtuple("Bill", "subtotal_paise discount_paise discount_label total_paise")
ShiftSummary = namedtuple("ShiftSummary", "shift cash_sales_paise expected_cash_paise difference_paise sales_paise")


def open_tab(*, table=None, member=None, customer_name="", opened_by=None):
    if table is None and member is None and not customer_name.strip():
        raise ValidationError("Choose a table, a member or enter a name for the tab.")
    try:
        with transaction.atomic():
            return Tab.objects.create(table=table, member=member, customer_name=customer_name.strip(), opened_by=opened_by)
    except IntegrityError as error:
        if "one_open_tab_per_table" not in str(error):
            raise
        raise ValidationError(f"Table {table.label} already has an open tab. Add to that tab instead.") from error


def _open_locked(tab):
    tab = Tab.objects.select_for_update().get(pk=tab.pk)
    if tab.status != Tab.Status.OPEN:
        raise ValidationError("This tab is already closed.")
    return tab


def add_item(tab, menu_item, quantity=1):
    """Lines can be added from any device; the price is frozen now so a later menu change doesn't alter the bill."""
    if quantity < 1:
        raise ValidationError("Quantity must be at least 1.")
    if not menu_item.is_available:
        raise ValidationError(f"{menu_item.name} is not available right now.")
    with transaction.atomic():
        tab = _open_locked(tab)
        return TabLine.objects.create(tab=tab, menu_item=menu_item, quantity=quantity, unit_price_paise=menu_item.price_paise)


def attach_member(tab, member):
    with transaction.atomic():
        tab = _open_locked(tab)
        tab.member = member
        tab.save(update_fields=["member"])
    return tab


def bill_for(tab):
    """The bill, with the member discount as its own visible line. Nobody has to remember to apply it."""
    subtotal = sum(line.quantity * line.unit_price_paise for line in tab.lines.all())
    membership = tab.member.current_membership if tab.member else None
    price = price_for("bar", subtotal, membership)
    discount = subtotal - price.amount_paise
    label = f"{membership.plan.name} member discount -{price.discount_pct}%" if discount else ""
    return Bill(subtotal, discount, label, price.amount_paise)


def settle_tab(tab, payments):
    """Close the tab with one or more (method, amount_paise) payments, e.g. split between cash and UPI.

    The payments must add up to exactly the bill. Each one is its own ledger row, so the day report
    shows how much came in by each method.
    """
    with transaction.atomic():
        tab = _open_locked(tab)  # two devices can't settle the same tab twice
        bill = bill_for(tab)
        payments = [(method, amount) for method, amount in payments if amount]
        for method, amount in payments:
            if method not in DESK_METHODS or amount < 0:
                raise ValidationError("Each payment needs cash, card or UPI and a positive amount.")
        paid = sum(amount for _, amount in payments)
        if paid != bill.total_paise:
            raise ValidationError(f"Payments add up to ₹{paid / 100:,.2f} but the bill is ₹{bill.total_paise / 100:,.2f}.")
        for method, amount in payments:
            record_payment(source=Source.BAR, method=method, amount_paise=amount, reference_id=tab.pk, note=f"Bar tab #{tab.pk}")
        tab.status = Tab.Status.PAID
        tab.closed_at = timezone.now()
        tab.discount_paise = bill.discount_paise
        tab.total_paise = bill.total_paise
        tab.save()
    return tab


def void_empty_tab(tab):
    """A tab opened by mistake can be closed only while nothing has been ordered on it."""
    with transaction.atomic():
        tab = _open_locked(tab)
        if tab.lines.exists():
            raise ValidationError("This tab has orders on it. Settle it instead.")
        tab.status = Tab.Status.VOID
        tab.closed_at = timezone.now()
        tab.save(update_fields=["status", "closed_at"])
    return tab


def kitchen_tickets(station):
    """New lines for one station (kitchen or bar), oldest first: what to make next."""
    return (
        TabLine.objects.filter(menu_item__station=station, progress=TabLine.Progress.NEW, tab__status=Tab.Status.OPEN)
        .select_related("menu_item", "tab__table", "tab__member")
        .order_by("ordered_at")
    )


def mark_line_ready(line):
    TabLine.objects.filter(pk=line.pk, progress=TabLine.Progress.NEW).update(progress=TabLine.Progress.READY)


def start_shift(staff, opening_cash_paise):
    try:
        with transaction.atomic():
            return Shift.objects.create(staff=staff, started_at=timezone.now(), opening_cash_paise=opening_cash_paise)
    except IntegrityError as error:
        raise ValidationError("You already have an open shift. End it first.") from error


def _bar_ledger(start, end):
    return Ledger.objects.filter(source=Source.BAR, created_at__gte=start, created_at__lt=end)


def end_shift(shift, closing_cash_paise, now=None):
    """Expected cash in the drawer = opening float + cash taken during the shift. The difference shows mistakes."""
    now = now or timezone.now()
    with transaction.atomic():
        shift = Shift.objects.select_for_update().get(pk=shift.pk)
        if shift.ended_at:
            raise ValidationError("This shift has already ended.")
        shift.ended_at = now
        shift.closing_cash_paise = closing_cash_paise
        shift.save(update_fields=["ended_at", "closing_cash_paise"])
    return shift_summary(shift)


def shift_summary(shift):
    end = shift.ended_at or timezone.now()
    rows = _bar_ledger(shift.started_at, end)
    cash = rows.filter(method=Method.CASH).aggregate(t=Sum("amount_paise"))["t"] or 0
    sales = rows.aggregate(t=Sum("amount_paise"))["t"] or 0
    expected = shift.opening_cash_paise + cash
    difference = (shift.closing_cash_paise - expected) if shift.closing_cash_paise is not None else None
    return ShiftSummary(shift, cash, expected, difference, sales)


def day_report(day):
    start, end = local_day_bounds(day)
    rows = _bar_ledger(start, end)
    by_method = {m: 0 for m in DESK_METHODS}
    for row in rows.values("method").annotate(total=Sum("amount_paise")):
        by_method[row["method"]] = row["total"]
    closed = Tab.objects.filter(status=Tab.Status.PAID, closed_at__gte=start, closed_at__lt=end)
    open_tabs = Tab.objects.filter(status=Tab.Status.OPEN).select_related("table", "member").prefetch_related("lines")
    return {
        "by_method": by_method,
        "total": sum(by_method.values()),
        "discounts": closed.aggregate(t=Sum("discount_paise"))["t"] or 0,
        "tabs_closed": closed.count(),
        "voided": Tab.objects.filter(status=Tab.Status.VOID, closed_at__gte=start, closed_at__lt=end).count(),
        "open_tabs": [(tab, bill_for(tab)) for tab in open_tabs],
    }
