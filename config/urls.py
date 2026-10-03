from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("desk/members/", include("members.urls")),
    path("desk/", include("courts.urls")),
    path("bar/", include("bar.urls")),
    path("", include("shop.urls")),
    path("", include("finance.urls")),
    path("", include("accounts.urls")),
]
