from datetime import date
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import role_required
from accounts.templatetags.money import rupees
from finance.models import Payment, Source
from finance.services import DESK_METHODS, OnlinePaymentUnavailable, start_online_payment
from members.pricing import price_for
from members.services import find_member_by_phone

from .forms import MenuItemForm
from .models import CafeOrder, CafeOrderLine, MenuItem, Shift, Tab, TabLine, Table
from .services import (
    MAX_PER_ITEM, add_item, attach_member, bill_for, cafe_tickets, cancel_cafe_order, collect_cafe_order, day_report,
    drop_unpaid_cafe_order, end_shift, kitchen_tickets, mark_cafe_line_ready, mark_line_ready, open_tab,
    place_cafe_order, save_menu_item, set_available, settle_tab, shift_summary, start_shift, void_empty_tab,
)

bar_staff = role_required("owner", "bar_staff")


def _rupees_to_paise(value):
    """'1,250.50' typed by staff -> 125050. Raises ValueError for anything else."""
    value = (value or "").replace(",", "").strip()
    if not value:
        return 0
    rupees_part, _, paise_part = value.partition(".")
    return int(rupees_part or 0) * 100 + int((paise_part + "00")[:2])


@bar_staff
def tables(request):
    open_tabs = {t.table_id: t for t in Tab.objects.filter(status=Tab.Status.OPEN).prefetch_related("lines")}
    table_rows = [(table, open_tabs.get(table.pk)) for table in Table.objects.order_by("label")]
    loose_tabs = [t for t in open_tabs.values() if t.table_id is None]
    return render(request, "bar/tables.html", {"table_rows": table_rows, "loose_tabs": loose_tabs})


@bar_staff
@require_POST
def tab_open(request):
    table = Table.objects.filter(pk=request.POST.get("table") or None).first()
    try:
        tab = open_tab(table=table, customer_name=request.POST.get("customer_name", ""), opened_by=request.user)
    except ValidationError as error:
        messages.error(request, error.messages[0])
        return redirect("bar_tables")
    return redirect("bar_tab", pk=tab.pk)


@bar_staff
def tab_detail(request, pk):
    tab = get_object_or_404(Tab.objects.select_related("table", "member"), pk=pk)
    menu = MenuItem.objects.filter(is_available=True).order_by("category", "name")
    return render(request, "bar/tab.html", {
        "tab": tab,
        "lines": tab.lines.select_related("menu_item").order_by("ordered_at"),
        "menu": [(category, list(items)) for category, items in groupby(menu, key=lambda m: m.category)],
        "bill": bill_for(tab),
        "methods": DESK_METHODS,
    })


@bar_staff
@require_POST
def tab_action(request, pk, action):
    tab = get_object_or_404(Tab, pk=pk)
    try:
        if action == "add":
            add_item(tab, get_object_or_404(MenuItem, pk=request.POST.get("item")), int(request.POST.get("quantity", 1)))
        elif action == "member":
            member = find_member_by_phone(request.POST.get("member_phone", ""))
            if member is None:
                raise ValidationError("No member has this phone number.")
            attach_member(tab, member)
            messages.success(request, f"{member.full_name} attached. Their discount is applied automatically.")
        elif action == "settle":
            payments = [
                (request.POST.get(f"method_{i}"), _rupees_to_paise(request.POST.get(f"amount_{i}")))
                for i in range(3)
            ]
            settle_tab(tab, payments)
            messages.success(request, f"Tab #{tab.pk} settled.")
            return redirect("bar_tables")
        elif action == "void":
            void_empty_tab(tab, by=request.user)
            messages.success(request, "Empty tab closed.")
            return redirect("bar_tables")
        else:
            messages.error(request, "Unknown action.")
    except ValueError:
        messages.error(request, "Enter amounts as numbers, like 450 or 450.50.")
    except ValidationError as error:
        messages.error(request, error.messages[0])
    return redirect("bar_tab", pk=tab.pk)


@bar_staff
def kitchen(request):
    station = request.GET.get("station", MenuItem.Station.KITCHEN)
    if station not in MenuItem.Station.values:
        station = MenuItem.Station.KITCHEN
    return render(request, "bar/kitchen.html", {
        "tickets": kitchen_tickets(station), "online_tickets": cafe_tickets(station), "station": station,
    })


@bar_staff
@require_POST
def ticket_ready(request, pk):
    mark_line_ready(get_object_or_404(TabLine, pk=pk))
    return redirect(f"/bar/kitchen/?station={request.POST.get('station', 'kitchen')}")


@bar_staff
def shift(request):
    current = Shift.objects.filter(staff=request.user, ended_at__isnull=True).first()
    if request.method == "POST":
        try:
            cash = _rupees_to_paise(request.POST.get("cash"))
            if current:
                summary = end_shift(current, cash)
                messages.success(request, f"Shift ended. Cash difference: {rupees(summary.difference_paise)}.")
            else:
                start_shift(request.user, cash)
                messages.success(request, "Shift started.")
        except ValueError:
            messages.error(request, "Enter the cash amount as a number.")
        except ValidationError as error:
            messages.error(request, error.messages[0])
        return redirect("bar_shift")
    recent = Shift.objects.filter(staff=request.user, ended_at__isnull=False).order_by("-started_at")[:5]
    return render(request, "bar/shift.html", {
        "current": shift_summary(current) if current else None,
        "recent": [shift_summary(s) for s in recent],
    })


@bar_staff
def report(request):
    try:
        day = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        day = timezone.localdate()
    return render(request, "bar/day_report.html", {"day": day, "report": day_report(day)})


@bar_staff
def menu(request):
    items = MenuItem.objects.order_by("category", "name")
    return render(request, "bar/menu.html", {
        "menu": [(category, list(group)) for category, group in groupby(items, key=lambda m: m.category)],
        "sold_out": sum(1 for item in items if not item.is_available),
    })


@bar_staff
def menu_edit(request, pk=None):
    item = get_object_or_404(MenuItem, pk=pk) if pk else MenuItem()
    form = MenuItemForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        item = save_menu_item(form.save(), by=request.user)
        messages.success(request, f"{item.name} saved.")
        return redirect("bar_menu")
    categories = MenuItem.objects.order_by("category").values_list("category", flat=True).distinct()
    return render(request, "bar/menu_edit.html", {"form": form, "item": item, "categories": categories})


@bar_staff
@require_POST
def menu_toggle(request, pk):
    item = get_object_or_404(MenuItem, pk=pk)
    set_available(item, not item.is_available)
    messages.success(request, f"{item.name} is {'sold out' if item.is_available else 'available again'}.")
    return redirect("bar_menu")


@bar_staff
@require_POST
def cafe_ticket_ready(request, pk):
    mark_cafe_line_ready(get_object_or_404(CafeOrderLine, pk=pk))
    return redirect(f"/bar/kitchen/?station={request.POST.get('station', 'kitchen')}")


@bar_staff
def online_orders(request):
    waiting = [CafeOrder.Status.PREPARING, CafeOrder.Status.READY]
    return render(request, "bar/online_orders.html", {
        "active": CafeOrder.objects.filter(status__in=waiting).prefetch_related("lines__menu_item").order_by("paid_at"),
        "recent": CafeOrder.objects.filter(status__in=[CafeOrder.Status.COLLECTED, CafeOrder.Status.CANCELLED],
                                           paid_at__isnull=False).order_by("-paid_at")[:15],
    })


@bar_staff
@require_POST
def online_order_action(request, pk, action):
    order = get_object_or_404(CafeOrder, pk=pk)
    try:
        if action == "collect":
            collect_cafe_order(order)
            messages.success(request, f"Order #{order.pk} collected.")
        elif action == "cancel":
            cancel_cafe_order(order, by=request.user)
            messages.success(request, f"Order #{order.pk} cancelled. {rupees(order.total_paise)} is being refunded online.")
        else:
            messages.error(request, "Unknown action.")
    except ValidationError as error:
        messages.error(request, error.messages[0])
    return redirect("bar_online_orders")


# ---- Customers: order from the cafe menu and pay online, collect at the counter ----

def _member_of(user):
    return getattr(user, "member", None) if user.is_authenticated else None


def _cafe_cart(request):
    return request.session.setdefault("cafe_cart", {})  # {"menu_item_id": quantity}


def _cafe_items(request):
    cart = _cafe_cart(request)
    items = MenuItem.objects.filter(pk__in=cart.keys()).order_by("category", "name")
    return [(item, cart[str(item.pk)]) for item in items]


@require_POST
def cafe_cart_add(request):
    item = MenuItem.objects.filter(pk=request.POST.get("item") or None, is_available=True).first()
    if item is None:
        messages.error(request, "Sorry, that item is sold out right now.")
        return redirect("cafe")
    cart = _cafe_cart(request)
    cart[str(item.pk)] = min(cart.get(str(item.pk), 0) + 1, MAX_PER_ITEM)
    request.session.modified = True  # the dict inside the session changed
    messages.success(request, f"Added {item.name} to your order.")
    return redirect("cafe")


def cafe_cart(request):
    if request.method == "POST":
        cart, key = _cafe_cart(request), request.POST.get("item", "")
        if key in cart:
            change = {"add": 1, "less": -1}.get(request.POST.get("change"), -MAX_PER_ITEM)  # anything else removes
            cart[key] = min(cart[key] + change, MAX_PER_ITEM)
            if cart[key] < 1:
                del cart[key]
            request.session.modified = True
        return redirect("cafe_cart")
    lines = [{"item": item, "quantity": q, "total": item.price_paise * q} for item, q in _cafe_items(request)]
    subtotal = sum(line["total"] for line in lines)
    member = _member_of(request.user)
    price = price_for("bar", subtotal, member.current_membership if member else None)
    return render(request, "bar/cafe_cart.html", {
        "lines": lines, "subtotal": subtotal, "discount": subtotal - price.amount_paise, "total": price.amount_paise,
        "discount_pct": price.discount_pct,
        "sold_out": [line["item"].name for line in lines if not line["item"].is_available],
    })


@login_required
@require_POST
def cafe_checkout(request):
    try:
        order = place_cafe_order(items=_cafe_items(request), placed_by=request.user, member=_member_of(request.user))
    except ValidationError as error:
        messages.error(request, error.messages[0])
        return redirect("cafe_cart")
    try:
        payment = start_online_payment(source=Source.BAR, reference_id=order.pk, amount_paise=order.total_paise)
    except OnlinePaymentUnavailable as error:
        drop_unpaid_cafe_order(order)
        messages.error(request, f"{error} Please order at the counter instead.")
        return redirect("cafe_cart")
    request.session["cafe_cart"] = {}
    return redirect("pay_page", pk=payment.pk)


@login_required
def cafe_my_orders(request):
    orders = list(
        CafeOrder.objects.filter(placed_by=request.user)
        .exclude(status=CafeOrder.Status.CANCELLED, paid_at__isnull=True)  # never-paid attempts are noise
        .prefetch_related("lines__menu_item").order_by("-created_at")[:20]
    )
    unpaid = [o.pk for o in orders if o.status == CafeOrder.Status.AWAITING_PAYMENT]
    payments = dict(Payment.objects.filter(source=Source.BAR, reference_id__in=unpaid, status=Payment.Status.CREATED)
                    .values_list("reference_id", "pk"))
    for order in orders:
        order.payment_pk = payments.get(order.pk)  # lets them finish paying
    return render(request, "bar/cafe_orders.html", {"orders": orders})
