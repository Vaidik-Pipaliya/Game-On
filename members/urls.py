from django.urls import path

from . import views

urlpatterns = [
    path("", views.member_search, name="member_search"),
    path("new/", views.member_new, name="member_new"),
    path("<int:pk>/", views.member_detail, name="member_detail"),
    path("<int:pk>/renew/", views.member_renew, name="member_renew"),
]
