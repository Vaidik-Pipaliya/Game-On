from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("plans/", views.plans, name="plans"),
    path("cafe/", views.cafe, name="cafe"),
    path("courts/availability/", views.availability, name="availability"),
    path("enquiry/", views.enquiry, name="enquiry"),
    path("trial/", views.trial, name="trial"),
    path("thanks/", views.thanks, name="enquiry_thanks"),
    path("desk/leads/", views.lead_list, name="lead_list"),
    path("desk/leads/<int:pk>/", views.lead_detail, name="lead_detail"),
]
