from datetime import date

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.forms import BootstrapFormMixin
from accounts.permissions import STAFF_ROLES, role_required

from .models import Employee, LeaveRequest, Payroll
from .services import ANNUAL_LEAVE_DAYS, DEDUCTION_PCT, decide_leave, leave_balance, pay_payroll, request_leave, run_payroll

owner_only = role_required("owner")
staff_only = role_required(*STAFF_ROLES)


class LeaveForm(BootstrapFormMixin, forms.Form):
    from_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="First day")
    to_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Last day")
    reason = forms.CharField(max_length=200, required=False)


def _month(value):
    try:
        year, month = (int(part) for part in value.split("-"))
        return date(year, month, 1)
    except (AttributeError, ValueError):
        return timezone.localdate().replace(day=1)


@owner_only
def payroll(request):
    month = _month(request.GET.get("month") or request.POST.get("month"))
    if request.method == "POST":
        created = run_payroll(month)
        messages.success(request, f"Payroll for {month:%B %Y}: {len(created)} new payslip(s).")
        return redirect(f"/owner/payroll/?month={month:%Y-%m}")
    rows = Payroll.objects.filter(month=month).select_related("employee").order_by("employee__full_name")
    totals = rows.aggregate(gross=Sum("gross_paise"), deduction=Sum("deduction_paise"), net=Sum("net_paise"))
    return render(request, "staffing/payroll.html", {
        "month": month, "rows": rows, "totals": totals, "deduction_pct": DEDUCTION_PCT,
        "unpaid": rows.filter(paid=False).aggregate(t=Sum("net_paise"))["t"] or 0,
    })


@owner_only
@require_POST
def payroll_pay(request, pk):
    row = get_object_or_404(Payroll, pk=pk)
    try:
        pay_payroll(row)
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        messages.success(request, f"Salary paid to {row.employee.full_name} (recorded as an expense).")
    return redirect(f"/owner/payroll/?month={row.month:%Y-%m}")


@owner_only
def leave_approvals(request):
    if request.method == "POST":
        leave = get_object_or_404(LeaveRequest, pk=request.POST.get("leave"))
        try:
            decide_leave(leave, approve=request.POST.get("decision") == "approve", decided_by=request.user)
        except ValidationError as error:
            messages.error(request, error.messages[0])
        else:
            messages.success(request, "Leave decision saved.")
        return redirect("leave_approvals")
    pending = LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING).select_related("employee").order_by("from_date")
    return render(request, "staffing/leave_approvals.html", {
        "pending": [(leave, leave_balance(leave.employee, leave.from_date.year)) for leave in pending],
        "decided": LeaveRequest.objects.exclude(status=LeaveRequest.Status.PENDING).select_related("employee", "decided_by").order_by("-from_date")[:30],
    })


@staff_only
def my_leave(request):
    employee = Employee.objects.filter(user=request.user).first()
    if employee is None:
        return render(request, "staffing/my_leave.html", {"employee": None})
    form = LeaveForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            request_leave(employee, data["from_date"], data["to_date"], data["reason"])
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            messages.success(request, "Leave requested. The owner will approve or reject it.")
            return redirect("my_leave")
    year = timezone.localdate().year
    return render(request, "staffing/my_leave.html", {
        "employee": employee, "form": form, "balance": leave_balance(employee, year), "allowance": ANNUAL_LEAVE_DAYS,
        "requests": employee.leaves.order_by("-from_date"),
    })
