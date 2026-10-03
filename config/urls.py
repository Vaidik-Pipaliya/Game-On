from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("desk/members/", include("members.urls")),
    path("", include("accounts.urls")),
]
