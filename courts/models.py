from django.conf import settings
from django.db import models

from members.models import Member


class Sport(models.Model):
    """Sports are data, not code: add padel or cricket nets from admin."""

    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class Court(models.Model):
    sport = models.ForeignKey(Sport, on_delete=models.PROTECT, related_name="courts")
    name = models.CharField(max_length=50)
    walk_in_rate_paise = models.PositiveIntegerField(help_text="Price of one 60-minute session for non-members")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.sport})"


class SocialSession(models.Model):
    """Friday social play: one court, many players, up to `capacity`."""

    court = models.ForeignKey(Court, on_delete=models.PROTECT)
    start = models.DateTimeField()
    end = models.DateTimeField()
    capacity = models.PositiveSmallIntegerField(default=12)
    price_per_player_paise = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Social {self.court} {self.start:%d %b %H:%M}"


class Booking(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = "confirmed"
        CANCELLED = "cancelled"
        COMPLETED = "completed"
        NO_SHOW = "no_show"

    class Kind(models.TextChoices):
        EXCLUSIVE = "exclusive", "Whole court"
        SOCIAL = "social", "Social play seat"

    court = models.ForeignKey(Court, on_delete=models.PROTECT, related_name="bookings")
    # Walk-ins and phone callers have no Member row, so we keep name + phone on the booking.
    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.PROTECT, related_name="bookings")
    guest_name = models.CharField(max_length=120, blank=True)
    guest_phone = models.CharField(max_length=15, blank=True)
    start = models.DateTimeField()
    end = models.DateTimeField()
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.EXCLUSIVE)
    social_session = models.ForeignKey(SocialSession, null=True, blank=True, on_delete=models.PROTECT)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CONFIRMED)
    # Price is frozen when the booking is made, so later plan changes never rewrite history.
    price_paise = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    # The ExclusionConstraint that blocks overlapping bookings is added in M5.

    def __str__(self):
        return f"{self.court} {self.start:%d %b %H:%M}"
