from django.urls import path

from . import views

urlpatterns = [
    path("", views.tables, name="bar_tables"),
    path("tabs/open/", views.tab_open, name="bar_tab_open"),
    path("tabs/<int:pk>/", views.tab_detail, name="bar_tab"),
    path("tabs/<int:pk>/<str:action>/", views.tab_action, name="bar_tab_action"),
    path("kitchen/", views.kitchen, name="bar_kitchen"),
    path("kitchen/<int:pk>/ready/", views.ticket_ready, name="bar_ticket_ready"),
    path("shift/", views.shift, name="bar_shift"),
    path("day-report/", views.report, name="bar_day_report"),
]
