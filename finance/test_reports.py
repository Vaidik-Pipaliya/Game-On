from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bar.models import MenuItem
from bar.services import add_item, open_tab
from courts.models import Booking
from courts.tests import Fixtures
from finance.analytics import court_utilisation, peak_hours, revenue_trend
from finance.models import Invoice
from finance.reports import amounts_owed, period_ranges, period_summary, revenue
from finance.services import record_expense, record_payment, record_refund

from config.clock import local_day_bounds

IST = ZoneInfo("Asia/Kolkata")
WEDNESDAY = date(2026, 10, 14)


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def pay(source, method, rupees, when):
    record_payment(source=source, method=method, amount_paise=rupees * 100, at=when)


class RevenueTests(TestCase):
    def test_split_by_source_and_method_net_of_refunds_without_expenses(self):
        day = WEDNESDAY
        pay("court", "cash", 800, at(day, 9))
        pay("court", "upi", 600, at(day, 10))
        pay("bar", "card", 900, at(day, 21))
        pay("membership", "card", 12000, at(day, 11))
        record_refund(source="court", method="upi", amount_paise=60000, at=at(day, 12))
        record_expense(amount_paise=2500000, method="online", note="Salaries", at=at(day, 13))
        r = revenue(*local_day_bounds(day))
        self.assertEqual(r["total"], (800 + 900 + 12000) * 100)
        self.assertEqual(r["by_source"], {"court": 80000, "shop": 0, "bar": 90000, "membership": 1200000})
        self.assertEqual(r["by_method"]["upi"], 0)
        self.assertEqual(r["matrix"]["membership"]["card"], 1200000)

    def test_club_day_follows_india_time_not_utc(self):
        pay("bar", "cash", 100, at(WEDNESDAY, 23, 59))  # 18:29 UTC, still Wednesday at the club
        pay("bar", "cash", 200, at(WEDNESDAY + timedelta(days=1), 0, 1))  # Wednesday in UTC, Thursday at the club
        self.assertEqual(revenue(*local_day_bounds(WEDNESDAY))["total"], 10000)


class PeriodTests(TestCase):
    def test_week_starts_on_monday_and_compares_same_weekdays(self):
        current, previous = period_ranges("week", WEDNESDAY)
        self.assertEqual(current, (date(2026, 10, 12), 3))
        self.assertEqual(previous, (date(2026, 10, 5), 3))

    def test_month_so_far_vs_same_days_last_month(self):
        self.assertEqual(period_ranges("month", WEDNESDAY), ((date(2026, 10, 1), 14), (date(2026, 9, 1), 14)))

    def test_short_previous_month_is_not_overrun(self):
        current, previous = period_ranges("month", date(2026, 3, 31))
        self.assertEqual(previous, (date(2026, 2, 1), 28))

    def test_today_vs_yesterday_change(self):
        pay("shop", "cash", 1000, at(WEDNESDAY - timedelta(days=1), 12))
        pay("shop", "cash", 1500, at(WEDNESDAY, 12))
        summary = period_summary("today", WEDNESDAY)
        self.assertEqual((summary["total"], summary["previous_total"], summary["change_pct"]), (150000, 100000, 50))

    def test_no_previous_sales_gives_no_percentage(self):
        pay("shop", "cash", 10, at(WEDNESDAY, 12))
        self.assertIsNone(period_summary("today", WEDNESDAY)["change_pct"])

    def test_month_total_matches_sum_of_daily_totals(self):
        # PRD FN-03 acceptance: dashboard totals match the daily close.
        for offset, amount in ((0, 100), (3, 250), (13, 75)):
            pay("bar", "cash", amount, at(date(2026, 10, 1) + timedelta(days=offset), 20))
        month = period_summary("month", WEDNESDAY)["total"]
        daily = sum(revenue(*local_day_bounds(date(2026, 10, 1) + timedelta(days=i)))["total"] for i in range(14))
        self.assertEqual(month, daily)


class OwedAndInvoiceTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()

    def test_gst_split_and_total(self):
        invoice = Invoice(number="1", customer_name="X", description="Gold", amount_paise=100000, gst_pct=18, issued_on=WEDNESDAY)
        self.assertEqual((invoice.gst_paise, invoice.cgst_paise, invoice.sgst_paise, invoice.total_paise), (18000, 9000, 9000, 118000))
        odd = Invoice(amount_paise=1001, gst_pct=5)
        self.assertEqual(odd.cgst_paise + odd.sgst_paise, odd.gst_paise)

    def test_amounts_owed_adds_up_each_kind(self):
        tab = open_tab(customer_name="Ravi")
        add_item(tab, MenuItem.objects.create(name="Tea", category="Drinks", price_paise=6000, station="kitchen"), 2)
        Booking.objects.create(court=self.court1, guest_name="G", start=at(WEDNESDAY, 18), end=at(WEDNESDAY, 19), price_paise=80000)
        Invoice.objects.create(number="CC-1", customer_name="Acme", description="Corporate", amount_paise=100000, issued_on=WEDNESDAY)
        owed = amounts_owed()
        self.assertEqual(owed["items"]["Open bar tabs"], (1, 12000))
        self.assertEqual(owed["items"]["Unpaid court bookings"], (1, 80000))
        self.assertEqual(owed["items"]["Unpaid invoices (incl. GST)"], (1, 118000))
        self.assertEqual(owed["total"], 12000 + 80000 + 118000)


class AnalyticsTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()

    def test_revenue_trend_has_every_day_including_empty_ones(self):
        pay("bar", "cash", 500, at(WEDNESDAY, 20))
        pay("bar", "cash", 300, at(WEDNESDAY - timedelta(days=2), 20))
        trend = revenue_trend(WEDNESDAY, days=30)
        self.assertEqual(len(trend["labels"]), 30)
        self.assertEqual(trend["rupees"][-1], 500)
        self.assertEqual(trend["rupees"][-2], 0)
        self.assertEqual(trend["rupees"][-3], 300)

    def test_utilisation_and_peak_hours(self):
        Booking.objects.create(court=self.court1, guest_name="G", start=at(WEDNESDAY, 18), end=at(WEDNESDAY, 19))
        Booking.objects.create(court=self.court1, guest_name="G", start=at(WEDNESDAY - timedelta(days=1), 18), end=at(WEDNESDAY - timedelta(days=1), 19))
        Booking.objects.create(court=self.court2, guest_name="G", start=at(WEDNESDAY, 7), end=at(WEDNESDAY, 8), status="cancelled")
        util = court_utilisation(WEDNESDAY, days=30)
        self.assertEqual(dict(zip(util["labels"], util["percent"])), {"Court 1": round(2 * 100 / (16 * 30), 1), "Court 2": 0.0})
        peaks = dict(zip(*peak_hours(WEDNESDAY, days=30).values()))
        self.assertEqual((peaks["18:00"], peaks["07:00"]), (2, 0))  # cancelled booking not counted


class DashboardScreenTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()
        self.owner = User.objects.create(username="o", email="o@example.com", role="owner")

    def test_only_the_owner_sees_the_dashboard_and_exports(self):
        self.client.force_login(User.objects.create(username="d", email="d@example.com", role="front_desk"))
        for name in ("owner_dashboard", "export_ledger", "export_bookings"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_dashboard_renders_numbers_and_chart_data(self):
        pay("court", "cash", 800, timezone.now())
        self.client.force_login(self.owner)
        response = self.client.get(reverse("owner_dashboard"), {"period": "today"})
        self.assertContains(response, "₹800")
        self.assertContains(response, "Total owed")
        self.assertContains(response, 'id="chart-data"')

    def test_ledger_csv(self):
        pay("bar", "upi", 450, timezone.now())
        self.client.force_login(self.owner)
        response = self.client.get(reverse("export_ledger"))
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        lines = response.content.decode().strip().splitlines()
        self.assertEqual(lines[0], "time,kind,source,method,amount_rupees,reference,note")
        self.assertIn(",payment,bar,upi,450.00,", lines[1])

    def test_csv_neutralises_formula_injection(self):
        now = timezone.localtime() + timedelta(hours=1)
        Booking.objects.create(court=self.court1, guest_name='=HYPERLINK("http://evil")', start=now, end=now + timedelta(hours=1))
        self.client.force_login(self.owner)
        body = self.client.get(reverse("export_bookings"), {"from": now.date().isoformat(), "to": now.date().isoformat()}).content.decode()
        self.assertIn("'=HYPERLINK", body)
