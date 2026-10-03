from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounts.audit import record
from accounts.models import AuditLog, User
from courts.services import cancel_booking
from courts.tests import NOW, Fixtures, at
from finance.invoicing import create_invoice, mark_invoice_paid
from shop.models import Product, Variant
from shop.services import cancel_order, place_order, restock
from staffing.models import Employee
from staffing.services import decide_leave, pay_payroll, request_leave, run_payroll


class AuditTrailTests(Fixtures, TestCase):
    def setUp(self):
        self.make_world()
        self.owner = User.objects.create(username="o", email="owner@example.com", role="owner", is_staff=True, is_superuser=True)

    def last(self):
        return AuditLog.objects.order_by("-id").first()

    def test_rows_cannot_be_edited_or_deleted(self):
        row = record(self.owner, "test.action", self.owner, "Something happened", before=1, after=2)
        self.assertEqual((row.target_type, row.details), ("accounts.User", {"before": 1, "after": 2}))
        with self.assertRaises(TypeError):
            row.save()
        with self.assertRaises(TypeError):
            row.delete()

    def test_booking_cancellation_with_refund_is_logged(self):
        booking = self.book(self.court1, at(10, 18), guest_name="G", guest_phone="1", payment_method="upi")
        cancel_booking(booking, now=NOW, by=self.owner)
        row = self.last()
        self.assertEqual((row.action, row.user, row.target_id), ("booking.cancel", self.owner, booking.pk))
        self.assertEqual((row.details["refund_paise"], row.details["method"]), (80000, "upi"))

    def test_stock_restock_logs_before_and_after(self):
        variant = Variant.objects.create(product=Product.objects.create(name="Grip", category="accessory", price_paise=100), stock=3)
        restock(variant, 5, by=self.owner)
        row = self.last()
        self.assertEqual((row.action, row.details), ("stock.restock", {"before": 3, "after": 8}))

    def test_order_cancel_payroll_leave_and_invoice_are_logged(self):
        variant = Variant.objects.create(product=Product.objects.create(name="Ball", category="ball", price_paise=5000), stock=5)
        order, _ = place_order(items=[(variant, 1)], channel="counter", payment_method="cash")
        order.status = "ready"
        order.save()
        cancel_order(order, by=self.owner)
        employee = Employee.objects.create(full_name="Meena", job_title="Desk", monthly_salary_paise=3000000, joined_on=date(2025, 1, 1))
        pay_payroll(run_payroll(date(2026, 10, 1))[0], by=self.owner)
        decide_leave(request_leave(employee, date(2026, 11, 2), date(2026, 11, 3)), approve=True, decided_by=self.owner)
        invoice = create_invoice(customer_name="X", description="Y", amount_paise=1000, gst_pct=18, issued_on=date(2026, 10, 3))
        mark_invoice_paid(invoice, "cash", by=self.owner)
        self.assertEqual(
            list(AuditLog.objects.order_by("id").values_list("action", flat=True)),
            ["order.cancel", "payroll.pay", "leave.approved", "invoice.paid"],
        )

    def test_role_change_in_admin_is_logged(self):
        member = User.objects.create(username="m", email="m@example.com", role="member")
        self.client.force_login(self.owner)
        self.client.post(reverse("admin:accounts_user_change", args=[member.pk]), {
            "username": "m", "email": "m@example.com", "role": "front_desk", "is_active": "on",
            "date_joined_0": "2026-01-01", "date_joined_1": "00:00:00",
        })
        row = self.last()
        self.assertEqual((row.action, row.details), ("user.role_change", {"before": "member", "after": "front_desk"}))

    def test_owner_sees_the_log_and_others_do_not(self):
        cancel_booking(self.book(self.court1, at(10, 18), guest_name="G", guest_phone="1"), now=NOW, by=self.owner)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("audit_log"), {"action": "booking"}), "Cancelled booking")
        self.client.force_login(User.objects.create(username="d", email="d@example.com", role="front_desk"))
        self.assertEqual(self.client.get(reverse("audit_log")).status_code, 403)

    def test_admin_cannot_delete_audit_rows(self):
        cancel_booking(self.book(self.court1, at(10, 18), guest_name="G", guest_phone="1"), now=NOW, by=self.owner)
        self.client.force_login(self.owner)
        row = self.last()
        self.assertEqual(self.client.post(f"/admin/accounts/auditlog/{row.pk}/delete/", {"post": "yes"}).status_code, 403)
