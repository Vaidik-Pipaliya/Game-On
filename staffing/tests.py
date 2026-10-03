from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from config.clock import local_day_bounds
from finance.models import Ledger
from finance.reports import expenses, revenue

from .models import Employee, LeaveRequest, Payroll
from .services import decide_leave, leave_balance, pay_payroll, request_leave, run_payroll

OCT = date(2026, 10, 1)


def employee(name="Meena", salary=3000000, joined=date(2025, 1, 1), user=None):
    return Employee.objects.create(full_name=name, job_title="Front desk", monthly_salary_paise=salary, joined_on=joined, user=user)


class PayrollTests(TestCase):
    def test_run_creates_net_after_flat_deduction_and_is_idempotent(self):
        employee()
        employee("Later Joiner", joined=date(2026, 11, 5))  # not yet employed in October
        created = run_payroll(OCT)
        self.assertEqual(len(created), 1)
        row = Payroll.objects.get()
        self.assertEqual((row.gross_paise, row.deduction_paise, row.net_paise), (3000000, 360000, 2640000))
        self.assertEqual(run_payroll(date(2026, 10, 15)), [])  # same month again: nothing new
        self.assertEqual(Payroll.objects.count(), 1)

    def test_paying_salary_is_an_expense_not_revenue(self):
        employee()
        row = run_payroll(OCT)[0]
        pay_payroll(row)
        with self.assertRaises(ValidationError):
            pay_payroll(row)
        expense = Ledger.objects.get()
        self.assertEqual((expense.kind, expense.amount_paise), ("expense", -2640000))
        today = local_day_bounds(timezone.localdate())
        self.assertEqual(revenue(*today)["total"], 0)
        self.assertEqual(expenses(*today), 2640000)


class LeaveTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(username="o", email="o@example.com", role="owner")
        self.emp = employee()

    def test_request_approve_and_balance(self):
        leave = request_leave(self.emp, date(2026, 10, 5), date(2026, 10, 7), "Family function")
        self.assertEqual(leave_balance(self.emp, 2026), 12)  # pending doesn't use the allowance yet
        decide_leave(leave, approve=True, decided_by=self.owner)
        self.assertEqual(leave_balance(self.emp, 2026), 9)
        with self.assertRaises(ValidationError):
            decide_leave(leave, approve=False, decided_by=self.owner)  # already decided

    def test_overlapping_and_backwards_requests_are_rejected(self):
        request_leave(self.emp, date(2026, 10, 5), date(2026, 10, 7))
        with self.assertRaisesMessage(ValidationError, "already have leave"):
            request_leave(self.emp, date(2026, 10, 7), date(2026, 10, 8))
        with self.assertRaises(ValidationError):
            request_leave(self.emp, date(2026, 10, 9), date(2026, 10, 8))

    def test_rejected_leave_frees_the_days(self):
        leave = request_leave(self.emp, date(2026, 10, 5), date(2026, 10, 7))
        decide_leave(leave, approve=False, decided_by=self.owner)
        request_leave(self.emp, date(2026, 10, 5), date(2026, 10, 7))  # allowed again

    def test_cannot_go_over_the_yearly_allowance(self):
        decide_leave(request_leave(self.emp, date(2026, 3, 1), date(2026, 3, 10)), approve=True, decided_by=self.owner)
        with self.assertRaisesMessage(ValidationError, "2 days remaining"):
            request_leave(self.emp, date(2026, 10, 1), date(2026, 10, 3))

    def test_approval_rechecks_the_balance(self):
        first = request_leave(self.emp, date(2026, 3, 1), date(2026, 3, 8))  # 8 days
        second = request_leave(self.emp, date(2026, 4, 1), date(2026, 4, 8))  # 8 days, fine on its own
        decide_leave(first, approve=True, decided_by=self.owner)
        with self.assertRaises(ValidationError):
            decide_leave(second, approve=True, decided_by=self.owner)
        self.assertEqual(LeaveRequest.objects.get(pk=second.pk).status, "pending")


class StaffingScreenTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(username="o", email="o@example.com", role="owner")
        self.bar_user = User.objects.create(username="b", email="b@example.com", role="bar_staff")
        self.emp = employee(user=self.bar_user)

    def test_staff_requests_leave_and_owner_approves(self):
        self.client.force_login(self.bar_user)
        self.client.post(reverse("my_leave"), {"from_date": "2026-12-01", "to_date": "2026-12-02", "reason": "Trip"})
        leave = LeaveRequest.objects.get()
        self.assertEqual(self.client.get(reverse("leave_approvals")).status_code, 403)
        self.client.force_login(self.owner)
        self.client.post(reverse("leave_approvals"), {"leave": leave.pk, "decision": "approve"})
        leave.refresh_from_db()
        self.assertEqual((leave.status, leave.decided_by), ("approved", self.owner))

    def test_payroll_screen_runs_and_pays(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("payroll"), {"month": "2026-10"})
        row = Payroll.objects.get()
        self.assertContains(self.client.get(reverse("payroll"), {"month": "2026-10"}), "Meena")
        self.client.post(reverse("payroll_pay", args=[row.pk]))
        self.assertTrue(Payroll.objects.get().paid)

    def test_staff_without_employee_record_sees_a_hint(self):
        self.client.force_login(User.objects.create(username="s", email="s@example.com", role="shop_staff"))
        self.assertContains(self.client.get(reverse("my_leave")), "isn't linked to an employee record")
