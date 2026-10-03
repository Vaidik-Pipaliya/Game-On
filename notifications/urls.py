from django.urls import path

from . import views

urlpatterns = [
    path("", views.log, name="notification_log"),
    path("retry/", views.retry, name="notification_retry_all"),
    path("<int:pk>/retry/", views.retry, name="notification_retry"),
]
