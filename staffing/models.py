from django.conf import settings
from django.db import models


class Employee(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    full_name = models.CharField(max_length=120)
    job_title = models.CharField(max_length=60)
    phone = models.CharField(max_length=15, blank=True)
    monthly_salary_paise = models.PositiveIntegerField()
    joined_on = models.DateField()

    def __str__(self):
        return self.full_name


class LeaveRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="leaves")
    from_date = models.DateField()
    to_date = models.DateField()
    reason = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)


class Payroll(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payrolls")
    month = models.DateField(help_text="First day of the month")
    gross_paise = models.PositiveIntegerField()
    deduction_paise = models.PositiveIntegerField(default=0)
    net_paise = models.PositiveIntegerField()
    paid = models.BooleanField(default=False)

    class Meta:
        unique_together = [("employee", "month")]
