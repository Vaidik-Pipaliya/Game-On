from django.urls import path

from . import views

# Customer pages under /cafe/ (the menu page itself, /cafe/, lives with the other public pages in crm).
urlpatterns = [
    path("cart/", views.cafe_cart, name="cafe_cart"),
    path("cart/add/", views.cafe_cart_add, name="cafe_cart_add"),
    path("checkout/", views.cafe_checkout, name="cafe_checkout"),
    path("orders/", views.cafe_my_orders, name="cafe_my_orders"),
]
