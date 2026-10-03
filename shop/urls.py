from django.urls import path

from . import views

urlpatterns = [
    path("shop/", views.catalog, name="shop_catalog"),
    path("shop/cart/", views.cart_view, name="shop_cart"),
    path("shop/cart/add/", views.cart_add, name="shop_cart_add"),
    path("shop/checkout/", views.checkout, name="shop_checkout"),
    path("shop/my-orders/", views.my_orders, name="shop_my_orders"),
    path("desk/shop/sale/", views.counter_sale, name="shop_counter"),
    path("desk/shop/orders/", views.orders_list, name="shop_orders"),
    path("desk/shop/orders/<int:pk>/<str:action>/", views.order_action, name="shop_order_action"),
    path("desk/shop/stock/", views.stock, name="shop_stock"),
]
