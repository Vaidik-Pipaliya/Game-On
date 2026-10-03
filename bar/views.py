from datetime import date
from itertools import groupby

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import role_required
from accounts.templatetags.money import rupees
from finance.services import DESK_METHODS
from members.services import find_member_by_phone

from .forms import MenuItemForm
from .models import MenuItem, Shift, Tab, TabLine, Table
from .services import (
    add_item, attach_member, bill_for, day_report, end_shift, kitchen_tickets, mark_line_ready, open_tab,
    save_menu_item, set_available, settle_tab, shift_summary, start_shift, void_empty_tab,
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
    return render(request, "bar/kitchen.html", {"tickets": kitchen_tickets(station), "station": station})


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
