"""GST invoices: sequential numbers, payment into the ledger, monthly GST summary."""

from collections import OrderedDict

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .models import Invoice
from .services import DESK_METHODS, record_payment

NUMBER_PREFIX = "CC"


def _next_number(year):
    last = Invoice.objects.filter(number__startswith=f"{NUMBER_PREFIX}/{year}/").order_by("-number").first()
    sequence = int(last.number.rsplit("/", 1)[1]) + 1 if last else 1
    return f"{NUMBER_PREFIX}/{year}/{sequence:04d}"


def create_invoice(*, customer_name, description, amount_paise, gst_pct, issued_on, member=None,
                   customer_gstin="", source="membership"):
    """Numbers run CC/2026/0001, 0002... per year with no gaps.

    Two invoices created at the same moment could compute the same next number; the unique
    constraint on `number` rejects the second, and we simply try again with the next number.
    """
    if gst_pct not in Invoice.GST_RATES:
        raise ValidationError("GST rate must be 0, 5, 12, 18 or 28%.")
    if amount_paise <= 0:
        raise ValidationError("Invoice amount must be more than zero.")
    for _ in range(5):
        try:
            with transaction.atomic():
                return Invoice.objects.create(
                    number=_next_number(issued_on.year), member=member, customer_name=customer_name.strip(),
                    customer_gstin=customer_gstin.strip().upper(), description=description.strip(), source=source,
                    amount_paise=amount_paise, gst_pct=gst_pct, issued_on=issued_on,
                )
        except IntegrityError:
            continue
    raise ValidationError("Could not number the invoice. Please try again.")


def mark_invoice_paid(invoice, method):
    if method not in DESK_METHODS:
        raise ValidationError("Choose cash, card or UPI.")
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)  # pay once, even on double-click
        if invoice.is_paid:
            raise ValidationError("This invoice is already paid.")
        record_payment(
            source=invoice.source, method=method, amount_paise=invoice.total_paise,
            reference_id=invoice.pk, note=f"Invoice {invoice.number}",
        )
        invoice.is_paid = True
        invoice.paid_method = method
        invoice.save(update_fields=["is_paid", "paid_method"])
    return invoice


def gst_summary(year, month):
    """Invoices issued in a month, grouped by GST rate: taxable value, CGST, SGST, total."""
    invoices = Invoice.objects.filter(issued_on__year=year, issued_on__month=month).order_by("gst_pct", "number")
    rows = OrderedDict()
    for invoice in invoices:
        row = rows.setdefault(invoice.gst_pct, {"rate": invoice.gst_pct, "count": 0, "taxable": 0, "cgst": 0, "sgst": 0, "total": 0})
        row["count"] += 1
        row["taxable"] += invoice.amount_paise
        row["cgst"] += invoice.cgst_paise
        row["sgst"] += invoice.sgst_paise
        row["total"] += invoice.total_paise
    totals = {key: sum(r[key] for r in rows.values()) for key in ("count", "taxable", "cgst", "sgst", "total")}
    return list(rows.values()), totals
