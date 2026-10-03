from django.urls import path

from . import views

urlpatterns = [
    path("book/", views.booking_grid, name="booking_grid"),
    path("book/new/", views.booking_new, name="booking_new"),
    path("bookings/<int:pk>/cancel/", views.booking_cancel, name="booking_cancel"),
    path("social/", views.social_list, name="social_list"),
    path("social/create/", views.social_create, name="social_create"),
    path("social/<int:pk>/join/", views.social_join, name="social_join"),
]
