"""Payroll (simple, monthly) and leave with a yearly allowance."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from accounts.audit import record as audit
from finance.services import record_expense

from .models import Employee, LeaveRequest, Payroll

DEDUCTION_PCT = 12  # flat deduction (PF-style) - statutory payroll (PF/ESI/TDS) is out of scope
ANNUAL_LEAVE_DAYS = 12


def month_end(month):
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def run_payroll(month):
    """Create this month's payroll rows. Safe to run twice: (employee, month) is unique, existing rows are kept."""
    month = month.replace(day=1)
    created = []
    with transaction.atomic():
        for employee in Employee.objects.filter(joined_on__lte=month_end(month)):
            gross = employee.monthly_salary_paise
            deduction = (gross * DEDUCTION_PCT + 50) // 100
            payroll, was_created = Payroll.objects.get_or_create(
                employee=employee, month=month,
                defaults={"gross_paise": gross, "deduction_paise": deduction, "net_paise": gross - deduction},
            )
            if was_created:
                created.append(payroll)
    return created


def pay_payroll(payroll, method="online", by=None):
    """Paying salary is an expense in the ledger (money out), never revenue."""
    with transaction.atomic():
        payroll = Payroll.objects.select_for_update().select_related("employee").get(pk=payroll.pk)
        if payroll.paid:
            raise ValidationError("This salary is already paid.")
        record_expense(
            amount_paise=payroll.net_paise, method=method, reference_id=payroll.pk,
            note=f"Salary {payroll.month:%b %Y}: {payroll.employee.full_name}",
        )
        payroll.paid = True
        payroll.save(update_fields=["paid"])
        audit(by, "payroll.pay", payroll, f"Paid {payroll.month:%b %Y} salary to {payroll.employee.full_name}", net_paise=payroll.net_paise)
    return payroll


def leave_days(start, end):
    return (end - start).days + 1


def leave_balance(employee, year):
    approved = employee.leaves.filter(status=LeaveRequest.Status.APPROVED, from_date__year=year)
    return ANNUAL_LEAVE_DAYS - sum(leave_days(l.from_date, l.to_date) for l in approved)


def _check_request(employee, from_date, to_date, exclude_pk=None):
    if to_date < from_date:
        raise ValidationError("The last day of leave can't be before the first day.")
    if from_date.year != to_date.year:
        raise ValidationError("Split leave that crosses the new year into two requests.")
    overlapping = employee.leaves.filter(
        ~Q(status=LeaveRequest.Status.REJECTED), from_date__lte=to_date, to_date__gte=from_date
    ).exclude(pk=exclude_pk)
    if overlapping.exists():
        raise ValidationError("You already have leave on some of these days.")
    if leave_days(from_date, to_date) > leave_balance(employee, from_date.year):
        raise ValidationError(f"Not enough leave left: {leave_balance(employee, from_date.year)} days remaining this year.")


def request_leave(employee, from_date, to_date, reason=""):
    _check_request(employee, from_date, to_date)
    return LeaveRequest.objects.create(employee=employee, from_date=from_date, to_date=to_date, reason=reason.strip())


def decide_leave(leave, *, approve, decided_by):
    with transaction.atomic():
        leave = LeaveRequest.objects.select_for_update().select_related("employee").get(pk=leave.pk)
        if leave.status != LeaveRequest.Status.PENDING:
            raise ValidationError("This request has already been decided.")
        if approve:
            # Check again: another request may have been approved since this one was made.
            _check_request(leave.employee, leave.from_date, leave.to_date, exclude_pk=leave.pk)
        leave.status = LeaveRequest.Status.APPROVED if approve else LeaveRequest.Status.REJECTED
        leave.decided_by = decided_by
        leave.save(update_fields=["status", "decided_by"])
        audit(decided_by, f"leave.{leave.status}", leave,
              f"{leave.status.capitalize()} leave for {leave.employee.full_name}: {leave.from_date:%d %b} to {leave.to_date:%d %b}")
    return leave
