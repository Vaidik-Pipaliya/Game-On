from django.urls import path

from . import views

urlpatterns = [
    path("owner/payroll/", views.payroll, name="payroll"),
    path("owner/payroll/<int:pk>/pay/", views.payroll_pay, name="payroll_pay"),
    path("owner/leave/", views.leave_approvals, name="leave_approvals"),
    path("staff/leave/", views.my_leave, name="my_leave"),
]
