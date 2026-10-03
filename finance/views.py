import csv
import json
from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.permissions import STAFF_ROLES, role_required
from config.clock import local_day_bounds
from courts.models import Booking
from shop.models import Order

from .analytics import court_utilisation, peak_hours, revenue_trend
from .forms import InvoiceForm
from .invoicing import create_invoice, gst_summary, mark_invoice_paid
from .models import Invoice, Ledger, Method, Payment, Source
from .reports import amounts_owed, expenses, period_ranges, period_summary
from .services import DESK_METHODS, checkout_signature_is_valid, mark_payment_captured, webhook_signature_is_valid


def _is_staff(user):
    return user.is_superuser or user.role in STAFF_ROLES


def _payment_for(request, pk):
    """Staff can open any payment; a customer only the payment for their own online shop order."""
    payment = get_object_or_404(Payment, pk=pk)
    if _is_staff(request.user):
        return payment
    if payment.source == Source.SHOP and Order.objects.filter(pk=payment.reference_id, placed_by=request.user).exists():
        return payment
    raise PermissionDenied


def _after_payment_url(request, payment):
    if payment.source == Source.COURT:
        booking = Booking.objects.filter(pk=payment.reference_id).first()
        if booking:
            return f"/desk/book/?date={timezone.localtime(booking.start).date().isoformat()}"
    if payment.source == Source.SHOP:
        return "/desk/shop/orders/" if _is_staff(request.user) else "/shop/my-orders/"
    return "/desk/"


@login_required
def pay_page(request, pk):
    payment = _payment_for(request, pk)
    if payment.status == Payment.Status.PAID:
        messages.info(request, "This payment is already complete.")
        return redirect(_after_payment_url(request, payment))
    return render(request, "finance/pay.html", {
        "payment": payment,
        "back_url": _after_payment_url(request, payment),
        "checkout": {  # only public values go to the browser; the secret never does
            "key": settings.RAZORPAY_KEY_ID,
            "order_id": payment.razorpay_order_id,
            "amount": payment.amount_paise,
            "currency": "INR",
            "name": "The Champions Club",
            "description": f"{payment.get_source_display()} #{payment.reference_id}",
        },
    })


@login_required
@require_POST
def pay_verify(request, pk):
    """Razorpay Checkout's success callback. We trust nothing the browser sends until the signature checks out."""
    payment = _payment_for(request, pk)
    payment_id = request.POST.get("razorpay_payment_id", "")
    signature = request.POST.get("razorpay_signature", "")
    if not checkout_signature_is_valid(payment.razorpay_order_id, payment_id, signature):
        return JsonResponse({"error": "Payment could not be verified. No money was recorded."}, status=400)
    mark_payment_captured(razorpay_order_id=payment.razorpay_order_id, razorpay_payment_id=payment_id)
    messages.success(request, "Payment received online.")
    return JsonResponse({"ok": True, "next": _after_payment_url(request, payment)})


@csrf_exempt  # Razorpay's servers can't send our CSRF token; the HMAC signature protects this endpoint instead
@require_POST
def razorpay_webhook(request):
    if not webhook_signature_is_valid(request.body, request.headers.get("X-Razorpay-Signature")):
        return HttpResponseBadRequest("Invalid signature")
    try:
        event = json.loads(request.body)
    except ValueError:
        return HttpResponseBadRequest("Invalid JSON")
    if event.get("event") == "payment.captured":
        entity = event["payload"]["payment"]["entity"]
        mark_payment_captured(
            razorpay_order_id=entity.get("order_id"), razorpay_payment_id=entity["id"], amount_paise=entity["amount"],
        )
    # Always 200 for a correctly signed event (even ones we ignore), so Razorpay stops retrying.
    return HttpResponse("ok")


# ---- Owner dashboard and exports ----

owner_only = role_required("owner")
PERIODS = (("today", "Today"), ("week", "This week"), ("month", "This month"))


@owner_only
def dashboard(request):
    today = timezone.localdate()
    period = request.GET.get("period", "month")
    if period not in dict(PERIODS):
        period = "month"
    summaries = [(key, label, period_summary(key, today)) for key, label in PERIODS]
    selected = dict((key, s) for key, _, s in summaries)[period]
    month_start, month_days = period_ranges("month", today)[0]
    return render(request, "finance/dashboard.html", {
        "today": today,
        "period": period,
        "periods": PERIODS,
        "summaries": summaries,
        "selected": selected,
        # source x method table as plain rows (templates can't index a dict by a variable)
        "methods": Method.choices,
        "matrix_rows": [
            (label, [selected["matrix"][source][m] for m in Method.values], selected["by_source"][source])
            for source, label in Source.choices
        ],
        "method_totals": [selected["by_method"][m] for m in Method.values],
        "owed": amounts_owed(),
        "month_expenses": expenses(*local_day_bounds(month_start, month_days)),
        "charts": {
            "trend": revenue_trend(today),
            "utilisation": court_utilisation(today),
            "peaks": peak_hours(today),
        },
    })


def _csv_safe(value):
    """Spreadsheets run cells starting with = + - @ as formulas; prefix text with ' so names can't inject one."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_csv_safe(v) for v in row])
    return response


def _date_range(request):
    today = timezone.localdate()
    try:
        first = date.fromisoformat(request.GET.get("from", ""))
    except ValueError:
        first = today.replace(day=1)
    try:
        last = date.fromisoformat(request.GET.get("to", ""))
    except ValueError:
        last = today
    if last < first:
        first, last = last, first
    return first, last


@owner_only
def export_ledger(request):
    first, last = _date_range(request)
    start, end = local_day_bounds(first, (last - first).days + 1)
    rows = (
        (timezone.localtime(r.created_at).strftime("%Y-%m-%d %H:%M"), r.kind, r.source, r.method,
         f"{r.amount_paise / 100:.2f}", r.reference_id or "", r.note)
        for r in Ledger.objects.filter(created_at__gte=start, created_at__lt=end).order_by("created_at")
    )
    return _csv_response(f"ledger_{first}_{last}.csv",
                         ["time", "kind", "source", "method", "amount_rupees", "reference", "note"], rows)


@owner_only
def export_bookings(request):
    first, last = _date_range(request)
    start, end = local_day_bounds(first, (last - first).days + 1)
    rows = (
        (timezone.localtime(b.start).strftime("%Y-%m-%d %H:%M"), b.court.name, b.kind, b.status,
         b.member.full_name if b.member else b.guest_name, f"{b.price_paise / 100:.2f}",
         "yes" if b.is_paid else "no", b.payment_method)
        for b in Booking.objects.filter(start__gte=start, start__lt=end).select_related("court", "member").order_by("start")
    )
    return _csv_response(f"bookings_{first}_{last}.csv",
                         ["start", "court", "kind", "status", "who", "price_rupees", "paid", "method"], rows)


# ---- Invoices and GST ----

def _parse_month(value, today):
    try:
        year, month = (int(part) for part in value.split("-"))
        return date(year, month, 1)
    except (AttributeError, ValueError):
        return today.replace(day=1)


@owner_only
def invoice_list(request):
    form = InvoiceForm(request.POST or None, initial={"issued_on": timezone.localdate(), "gst_pct": 18})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            invoice = create_invoice(
                customer_name=data["customer_name"], customer_gstin=data["customer_gstin"],
                description=data["description"], amount_paise=round(data["amount_rupees"] * 100),
                gst_pct=int(data["gst_pct"]), issued_on=data["issued_on"], source=data["source"],
                member=data["member_phone"],
            )
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            messages.success(request, f"Invoice {invoice.number} created.")
            return redirect("invoice_detail", pk=invoice.pk)
    invoices = Invoice.objects.order_by("-issued_on", "-number")[:50]
    return render(request, "finance/invoices.html", {"form": form, "invoices": invoices})


@owner_only
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method == "POST":
        try:
            mark_invoice_paid(invoice, request.POST.get("payment_method"), by=request.user)
        except ValidationError as error:
            messages.error(request, error.messages[0])
        else:
            messages.success(request, f"Invoice {invoice.number} marked paid.")
        return redirect("invoice_detail", pk=pk)
    return render(request, "finance/invoice_detail.html", {"invoice": invoice, "club": settings.CLUB, "methods": DESK_METHODS})


@owner_only
def gst_report(request):
    month = _parse_month(request.GET.get("month"), timezone.localdate())
    rows, totals = gst_summary(month.year, month.month)
    if request.GET.get("format") == "csv":
        lines = [(f"{r['rate']}%", r["count"], *(f"{r[k] / 100:.2f}" for k in ("taxable", "cgst", "sgst", "total"))) for r in rows]
        return _csv_response(f"gst_{month:%Y_%m}.csv", ["gst_rate", "invoices", "taxable", "cgst", "sgst", "total"], lines)
    return render(request, "finance/gst.html", {"month": month, "rows": rows, "totals": totals})
