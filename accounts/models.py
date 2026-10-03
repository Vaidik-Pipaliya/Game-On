from django.conf import settings
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


class AuditLog(models.Model):
    """Append-only record of sensitive actions: refunds, stock changes, payroll, leave decisions, role changes."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=40, db_index=True)  # e.g. "booking.cancel"
    target_type = models.CharField(max_length=60)  # e.g. "courts.Booking"
    target_id = models.PositiveIntegerField()
    summary = models.CharField(max_length=300)
    details = models.JSONField(default=dict, blank=True)  # before/after values

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise TypeError("Audit rows are never edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError("Audit rows are never deleted.")
