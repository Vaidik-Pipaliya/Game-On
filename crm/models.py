from django.conf import settings
from django.db import models

from courts.models import Booking
from members.models import Member


class Lead(models.Model):
    class Status(models.TextChoices):
        NEW = "new"
        CONTACTED = "contacted"
        QUOTED = "quoted"
        WON = "won"
        LOST = "lost"

    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=15)
    email = models.EmailField(blank=True)
    sport_interest = models.CharField(max_length=50, blank=True)
    message = models.TextField(blank=True)
    source = models.CharField(max_length=20, default="website")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW)
    lost_reason = models.CharField(max_length=200, blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    converted_member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.SET_NULL)
    trial_booking = models.ForeignKey(Booking, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_open(self):
        return self.status not in (self.Status.WON, self.Status.LOST)

    def __str__(self):
        return f"{self.name} ({self.status})"
