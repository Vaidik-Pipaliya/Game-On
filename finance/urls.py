from django.urls import path

from . import views

urlpatterns = [
    path("pay/<int:pk>/", views.pay_page, name="pay_page"),
    path("pay/<int:pk>/verify/", views.pay_verify, name="pay_verify"),
    path("webhooks/razorpay/", views.razorpay_webhook, name="razorpay_webhook"),
]
