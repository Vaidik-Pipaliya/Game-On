"""Open every page with the full demo data loaded. Catches template errors and 500s that unit tests miss."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from bar.models import Tab
from finance.models import Invoice
from members.models import Member
from staffing.models import Employee


class EveryPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        cls.owner = User.objects.create(username="o", email="owner@example.com", role="owner", is_staff=True, is_superuser=True)
        employee = Employee.objects.first()
        employee.user = cls.owner
        employee.save()

    def assertPages(self, urls):
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, f"{url} returned {response.status_code}")

    def test_public_pages(self):
        self.assertPages([reverse(n) for n in ("home", "plans", "availability", "enquiry", "shop_catalog", "shop_cart", "login", "enquiry_thanks")]
                         + [reverse("shop_catalog") + "?category=shoes"])

    def test_staff_and_owner_pages(self):
        self.client.force_login(self.owner)
        member = Member.objects.exclude(memberships=None).first()
        tab = Tab.objects.filter(status="open").first()
        invoice = Invoice.objects.first()
        month = timezone.localdate().strftime("%Y-%m")
        self.assertPages([
            reverse("desk"), reverse("booking_grid"), reverse("booking_grid") + "?sport=1", reverse("social_list"),
            reverse("member_search"), reverse("member_search") + "?q=a", reverse("member_new"), reverse("member_detail", args=[member.pk]),
            reverse("lead_list"), reverse("lead_list") + "?status=lost&mine=1", reverse("notification_log"),
            reverse("shop_counter"), reverse("shop_orders"), reverse("shop_stock"), reverse("shop_my_orders"),
            reverse("bar_tables"), reverse("bar_kitchen"), reverse("bar_kitchen") + "?station=bar", reverse("bar_shift"),
            reverse("bar_day_report"), reverse("bar_tab", args=[tab.pk]),
            reverse("owner_dashboard"), reverse("owner_dashboard") + "?period=today", reverse("export_ledger"), reverse("export_bookings"),
            reverse("invoice_list"), reverse("invoice_detail", args=[invoice.pk]), reverse("gst_report") + f"?month={month}",
            reverse("payroll") + f"?month={month}", reverse("leave_approvals"), reverse("my_leave"),
        ])

    def test_trial_page_needs_sign_in_then_opens(self):
        self.assertEqual(self.client.get(reverse("trial")).status_code, 302)
        visitor = User.objects.create(username="v", email="visitor@example.com", role="member")
        self.client.force_login(visitor)
        self.assertPages([reverse("trial")])

    def test_member_portal_pages(self):
        member = Member.objects.exclude(memberships=None).first()
        member.user = self.owner
        member.save()
        self.client.force_login(self.owner)
        self.assertPages([reverse("portal_grid"), reverse("portal_mine"), reverse("audit_log")])

    def test_lead_detail_and_django_admin(self):
        from crm.models import Lead
        self.client.force_login(self.owner)
        self.assertPages([reverse("lead_detail", args=[Lead.objects.first().pk]), "/admin/", "/admin/finance/ledger/", "/admin/courts/booking/"])

    def test_nothing_in_the_ledger_can_be_edited_in_admin(self):
        self.client.force_login(self.owner)
        from finance.models import Ledger
        row = Ledger.objects.first()
        self.assertEqual(self.client.post(f"/admin/finance/ledger/{row.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.assertTrue(Ledger.objects.filter(pk=row.pk).exists())
