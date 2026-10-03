from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_page, name="login"),
    path("auth/firebase/", views.firebase_login, name="firebase_login"),
    path("logout/", views.logout_view, name="logout"),
    path("desk/", views.desk, name="desk"),
]
