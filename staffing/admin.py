from django.contrib import admin

from .models import Employee, LeaveRequest, Payroll

admin.site.register([Employee, LeaveRequest, Payroll])
