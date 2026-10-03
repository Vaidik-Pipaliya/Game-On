from django.urls import path

from . import views

urlpatterns = [
    path("pay/<int:pk>/", views.pay_page, name="pay_page"),
    path("pay/<int:pk>/verify/", views.pay_verify, name="pay_verify"),
    path("webhooks/razorpay/", views.razorpay_webhook, name="razorpay_webhook"),
    path("owner/dashboard/", views.dashboard, name="owner_dashboard"),
    path("owner/export/ledger.csv", views.export_ledger, name="export_ledger"),
    path("owner/export/bookings.csv", views.export_bookings, name="export_bookings"),
    path("owner/refunds/retry/", views.retry_refunds, name="retry_refunds"),
    path("owner/invoices/", views.invoice_list, name="invoice_list"),
    path("owner/invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("owner/gst/", views.gst_report, name="gst_report"),
]
