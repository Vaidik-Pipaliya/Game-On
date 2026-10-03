from django.db import models

from courts.models import Booking
from members.models import Member


class Notification(models.Model):
    """Log of every WhatsApp/email we try to send, so failures can be retried."""

    class Channel(models.TextChoices):
        WHATSAPP = "whatsapp"
        EMAIL = "email"

    class Status(models.TextChoices):
        PENDING = "pending"
        SENT = "sent"
        FAILED = "failed"

    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.SET_NULL)
    booking = models.ForeignKey(Booking, null=True, blank=True, on_delete=models.SET_NULL)
    template = models.CharField(max_length=40)
    channel = models.CharField(max_length=10, choices=Channel.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
