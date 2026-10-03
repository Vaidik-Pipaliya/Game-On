"""Owner numbers. Revenue is read from the Ledger only, so the dashboard always matches the daily close."""

from datetime import timedelta

from django.db.models import Sum

from bar.models import Tab
from bar.services import bill_for
from config.clock import local_day_bounds
from courts.models import Booking
from shop.models import Order

from .models import Invoice, Ledger, Method, Source

REVENUE_KINDS = (Ledger.Kind.PAYMENT, Ledger.Kind.REFUND)  # expenses are money out, not revenue


def revenue(start, end):
    """Net revenue (payments minus refunds) in [start, end), split by source, by method and by both."""
    rows = (
        Ledger.objects.filter(kind__in=REVENUE_KINDS, created_at__gte=start, created_at__lt=end)
        .values("source", "method").annotate(total=Sum("amount_paise"))
    )
    by_source = {s: 0 for s in Source.values}
    by_method = {m: 0 for m in Method.values}
    matrix = {s: {m: 0 for m in Method.values} for s in Source.values}
    for row in rows:
        by_source[row["source"]] += row["total"]
        by_method[row["method"]] += row["total"]
        matrix[row["source"]][row["method"]] += row["total"]
    return {"total": sum(by_source.values()), "by_source": by_source, "by_method": by_method, "matrix": matrix}


def period_ranges(period, today):
    """(current [start, end), previous [start, end)) as club-local dates; "so far" compared like for like.

    today: today vs yesterday. week: Monday..today vs the same weekdays last week.
    month: 1st..today vs the same number of days at the start of last month.
    """
    if period == "today":
        return (today, 1), (today - timedelta(days=1), 1)
    if period == "week":
        start = today - timedelta(days=today.weekday())
        days = (today - start).days + 1
        return (start, days), (start - timedelta(days=7), days)
    if period == "month":
        start = today.replace(day=1)
        days = (today - start).days + 1
        previous_start = (start - timedelta(days=1)).replace(day=1)
        previous_days = min(days, (start - previous_start).days)  # don't run past the end of a short month
        return (start, days), (previous_start, previous_days)
    raise ValueError(f"Unknown period: {period}")


def period_summary(period, today):
    (current_start, current_days), (previous_start, previous_days) = period_ranges(period, today)
    current = revenue(*local_day_bounds(current_start, current_days))
    previous = revenue(*local_day_bounds(previous_start, previous_days))["total"]
    change = None if previous == 0 else round((current["total"] - previous) * 100 / abs(previous))
    return {**current, "previous_total": previous, "change_pct": change, "from": current_start}


def expenses(start, end):
    return -(Ledger.objects.filter(kind=Ledger.Kind.EXPENSE, created_at__gte=start, created_at__lt=end)
             .aggregate(t=Sum("amount_paise"))["t"] or 0)


def amounts_owed():
    """Money the club is still waiting for."""
    open_tabs = [bill_for(tab).total_paise for tab in Tab.objects.filter(status=Tab.Status.OPEN).prefetch_related("lines")]
    unpaid_bookings = Booking.objects.filter(status=Booking.Status.CONFIRMED, is_paid=False, price_paise__gt=0)
    unpaid_orders = Order.objects.filter(status__in=[Order.Status.PLACED, Order.Status.READY], is_paid=False)
    unpaid_invoices = list(Invoice.objects.filter(is_paid=False))
    items = {
        "Open bar tabs": (len(open_tabs), sum(open_tabs)),
        "Unpaid court bookings": (unpaid_bookings.count(), unpaid_bookings.aggregate(t=Sum("price_paise"))["t"] or 0),
        "Unpaid shop orders": (unpaid_orders.count(), unpaid_orders.aggregate(t=Sum("total_paise"))["t"] or 0),
        "Unpaid invoices (incl. GST)": (len(unpaid_invoices), sum(i.total_paise for i in unpaid_invoices)),
    }
    return {"items": items, "total": sum(amount for _, amount in items.values())}
