"""Bar and cafe rules: tabs, kitchen tickets, automatic member discount, split payments, shifts, day report."""

from collections import namedtuple

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.audit import record as audit
from accounts.templatetags.money import rupees
from config.clock import local_day_bounds
from finance.models import Ledger, Method, Source
from finance.services import DESK_METHODS, record_payment, record_refund, request_gateway_refund
from members.pricing import price_for

from .models import CafeOrder, CafeOrderLine, MenuItem, Shift, Tab, TabLine

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
            raise ValidationError(f"Payments add up to {rupees(paid)} but the bill is {rupees(bill.total_paise)}.")
        for method, amount in payments:
            record_payment(source=Source.BAR, method=method, amount_paise=amount, reference_id=tab.pk, note=f"Bar tab #{tab.pk}")
        tab.status = Tab.Status.PAID
        tab.closed_at = timezone.now()
        tab.discount_paise = bill.discount_paise
        tab.total_paise = bill.total_paise
        tab.save()
    return tab


def void_empty_tab(tab, by=None):
    """A tab opened by mistake can be closed only while nothing has been ordered on it."""
    with transaction.atomic():
        tab = _open_locked(tab)
        if tab.lines.exists():
            raise ValidationError("This tab has orders on it. Settle it instead.")
        tab.status = Tab.Status.VOID
        tab.closed_at = timezone.now()
        tab.save(update_fields=["status", "closed_at"])
        audit(by, "tab.void", tab, f"Voided empty tab #{tab.pk} ({tab.who})")
    return tab


def save_menu_item(item, by=None):
    """Add or edit a menu item. Price changes go in the audit log.

    Lines already on a tab keep the price they were ordered at (TabLine.unit_price_paise), so a price
    change never alters an open bill.
    """
    with transaction.atomic():
        before = None
        if item.pk:
            before = MenuItem.objects.select_for_update().values_list("price_paise", flat=True).get(pk=item.pk)
        item.save()
        if before is None:
            audit(by, "menu.add", item, f"Added {item.name} at {rupees(item.price_paise)}")
        elif before != item.price_paise:
            audit(by, "menu.price", item, f"{item.name}: {rupees(before)} -> {rupees(item.price_paise)}",
                  before=before, after=item.price_paise)
    return item


def set_available(item, available):
    """Mark an item sold out (or back on). Sold-out items disappear from the tab screen and show as sold out online."""
    MenuItem.objects.filter(pk=item.pk).update(is_available=available)


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


# ---- Cafe orders placed and paid online, collected at the counter ----

MAX_PER_ITEM = 10


def place_cafe_order(*, items, placed_by, member=None, customer_name=""):
    """items = [(menu_item, quantity)]. Prices and the member discount are frozen now; payment comes next.

    The discount is worked out on the whole order, exactly like a tab's bill, so it matches the counter.
    """
    if not items:
        raise ValidationError("Your order is empty.")
    for item, quantity in items:
        if not 1 <= quantity <= MAX_PER_ITEM:
            raise ValidationError(f"Order between 1 and {MAX_PER_ITEM} of {item.name}.")
    with transaction.atomic():
        fresh = {m.pk: m for m in MenuItem.objects.filter(pk__in=[item.pk for item, _ in items])}
        sold_out = [item.name for item, _ in items if item.pk not in fresh or not fresh[item.pk].is_available]
        if sold_out:
            raise ValidationError(f"Sold out right now: {', '.join(sold_out)}. Remove it and try again.")
        subtotal = sum(fresh[item.pk].price_paise * quantity for item, quantity in items)
        membership = member.current_membership if member else None
        price = price_for("bar", subtotal, membership)
        order = CafeOrder.objects.create(
            placed_by=placed_by, member=member,
            customer_name=customer_name or (member.full_name if member else placed_by.email),
            subtotal_paise=subtotal, discount_paise=subtotal - price.amount_paise, total_paise=price.amount_paise,
        )
        CafeOrderLine.objects.bulk_create([
            CafeOrderLine(order=order, menu_item=fresh[item.pk], quantity=quantity, unit_price_paise=fresh[item.pk].price_paise)
            for item, quantity in items
        ])
    return order


def drop_unpaid_cafe_order(order):
    """Online payment couldn't even start (gateway down): the order never happened. No money was taken."""
    CafeOrder.objects.filter(pk=order.pk, status=CafeOrder.Status.AWAITING_PAYMENT).update(status=CafeOrder.Status.CANCELLED)


def apply_cafe_payment(order_id):
    """Called by finance.mark_payment_captured, inside its transaction, once Razorpay confirms the money.

    Only now does the order go to the kitchen. The ledger row is written by the caller.
    """
    order = CafeOrder.objects.select_for_update().filter(pk=order_id).first()
    if order and order.status == CafeOrder.Status.AWAITING_PAYMENT:
        order.status = CafeOrder.Status.PREPARING
        order.paid_at = timezone.now()
        order.save(update_fields=["status", "paid_at"])


def cafe_tickets(station):
    """Paid online-order lines still to make at one station, oldest first."""
    return (
        CafeOrderLine.objects.filter(menu_item__station=station, is_ready=False, order__status=CafeOrder.Status.PREPARING)
        .select_related("menu_item", "order")
        .order_by("order__paid_at", "pk")
    )


def mark_cafe_line_ready(line):
    """When the last line of an order is ready, the whole order becomes ready to collect."""
    with transaction.atomic():
        order = CafeOrder.objects.select_for_update().get(pk=line.order_id)
        CafeOrderLine.objects.filter(pk=line.pk).update(is_ready=True)
        if order.status == CafeOrder.Status.PREPARING and not order.lines.filter(is_ready=False).exists():
            order.status = CafeOrder.Status.READY
            order.save(update_fields=["status"])


def _paid_order_locked(order):
    order = CafeOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in (CafeOrder.Status.PREPARING, CafeOrder.Status.READY):
        raise ValidationError(f"Order #{order.pk} is {order.get_status_display().lower()}.")
    return order


def collect_cafe_order(order):
    with transaction.atomic():
        order = _paid_order_locked(order)
        order.status = CafeOrder.Status.COLLECTED
        order.save(update_fields=["status"])
    return order


def cancel_cafe_order(order, by=None):
    """Cancel a paid order and give the money back through Razorpay (full refund).

    The ledger refund and the "refund pending" mark are saved together with the cancellation; Razorpay
    is asked after the commit, so a gateway problem can't block it (the owner can retry from the dashboard).
    """
    with transaction.atomic():
        order = _paid_order_locked(order)
        order.status = CafeOrder.Status.CANCELLED
        order.save(update_fields=["status"])
        record_refund(source=Source.BAR, method=Method.ONLINE, amount_paise=order.total_paise,
                      reference_id=order.pk, note=f"Cafe online order #{order.pk} cancelled")
        request_gateway_refund(source=Source.BAR, reference_id=order.pk)
        audit(by, "cafe_order.cancel", order, f"Cancelled cafe order #{order.pk} and refunded {rupees(order.total_paise)}",
              amount_paise=order.total_paise)
    return order
