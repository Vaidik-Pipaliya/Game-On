from django.urls import path

from . import portal

urlpatterns = [
    path("", portal.grid, name="portal_grid"),
    path("confirm/", portal.confirm, name="portal_confirm"),
    path("mine/", portal.mine, name="portal_mine"),
    path("<int:pk>/cancel/", portal.cancel, name="portal_cancel"),
    path("<int:pk>/pay/", portal.pay, name="portal_pay"),
]
