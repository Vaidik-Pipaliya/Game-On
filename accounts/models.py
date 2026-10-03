from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Login account. Email is the identity (Google login in M2); role drives permissions."""

    class Role(models.TextChoices):
        OWNER = "owner"
        FRONT_DESK = "front_desk"
        BAR_STAFF = "bar_staff"
        SHOP_STAFF = "shop_staff"
        MEMBER = "member"

    email = models.EmailField(unique=True)
    # New Google sign-ins start as plain members; the owner promotes staff in admin.
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
