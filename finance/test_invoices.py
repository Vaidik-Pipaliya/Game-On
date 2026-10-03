from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from config.clock import local_day_bounds

from .invoicing import create_invoice, gst_summary, mark_invoice_paid
from .models import Invoice, Ledger
from .reports import revenue


def make_invoice(amount=100000, gst=18, issued_on=date(2026, 10, 3), **kwargs):
    return create_invoice(customer_name="Acme Corp", description="Corporate plan", amount_paise=amount,
                          gst_pct=gst, issued_on=issued_on, **kwargs)


class InvoiceTests(TestCase):
    def test_numbers_are_sequential_per_year(self):
        numbers = [make_invoice().number, make_invoice().number, make_invoice(issued_on=date(2027, 1, 2)).number]
        self.assertEqual(numbers, ["CC/2026/0001", "CC/2026/0002", "CC/2027/0001"])

    def test_number_clash_is_retried(self):
        # Simulate another request taking 0002 between our read and our insert.
        make_invoice()
        Invoice.objects.create(number="CC/2026/0002", customer_name="X", description="X", amount_paise=1, issued_on=date(2026, 10, 3))
        self.assertEqual(make_invoice().number, "CC/2026/0003")

    def test_only_real_gst_rates_and_positive_amounts(self):
        with self.assertRaises(ValidationError):
            make_invoice(gst=7)
        with self.assertRaises(ValidationError):
            make_invoice(amount=0)

    def test_gst_split_for_5_percent(self):
        invoice = make_invoice(amount=100100, gst=5)  # ₹1,001 -> GST 5005 paise
        self.assertEqual((invoice.gst_paise, invoice.cgst_paise, invoice.sgst_paise, invoice.half_rate), (5005, 2502, 2503, "2.5"))

    def test_mark_paid_records_total_incl_gst_once(self):
        invoice = make_invoice(source="membership")
        mark_invoice_paid(invoice, "upi")
        with self.assertRaises(ValidationError):
            mark_invoice_paid(invoice, "upi")
        row = Ledger.objects.get()
        self.assertEqual((row.source, row.method, row.amount_paise), ("membership", "upi", 118000))

    def test_gst_summary_groups_by_rate(self):
        make_invoice(amount=100000, gst=18)
        make_invoice(amount=200000, gst=18)
        make_invoice(amount=50000, gst=5)
        make_invoice(amount=99999, gst=18, issued_on=date(2026, 11, 1))  # other month
        rows, totals = gst_summary(2026, 10)
        self.assertEqual([(r["rate"], r["count"], r["taxable"], r["cgst"] + r["sgst"]) for r in rows],
                         [(5, 1, 50000, 2500), (18, 2, 300000, 54000)])
        self.assertEqual((totals["count"], totals["total"]), (3, 350000 + 56500))


class InvoiceScreenTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(username="o", email="o@example.com", role="owner")
        self.client.force_login(self.owner)

    def test_create_view_converts_rupees_to_paise(self):
        response = self.client.post(reverse("invoice_list"), {
            "customer_name": "Acme Corp", "customer_gstin": "", "member_phone": "", "description": "Corporate plan",
            "source": "membership", "amount_rupees": "1250.50", "gst_pct": "18", "issued_on": "2026-10-03",
        })
        invoice = Invoice.objects.get()
        self.assertRedirects(response, reverse("invoice_detail", args=[invoice.pk]))
        self.assertEqual(invoice.amount_paise, 125050)
        page = self.client.get(reverse("invoice_detail", args=[invoice.pk]))
        self.assertContains(page, "CGST 9%")
        self.assertContains(page, "Print or save as PDF")

    def test_gst_csv_and_owner_only(self):
        make_invoice()
        csv = self.client.get(reverse("gst_report"), {"month": "2026-10", "format": "csv"}).content.decode()
        self.assertIn("18%,1,1000.00,90.00,90.00,1180.00", csv)
        self.client.force_login(User.objects.create(username="d", email="d@example.com", role="front_desk"))
        self.assertEqual(self.client.get(reverse("invoice_list")).status_code, 403)

    def test_paid_invoice_counts_as_revenue_on_the_dashboard(self):
        invoice = make_invoice(issued_on=timezone.localdate())
        self.client.post(reverse("invoice_detail", args=[invoice.pk]), {"payment_method": "card"})
        self.assertEqual(revenue(*local_day_bounds(timezone.localdate()))["by_source"]["membership"], 118000)
