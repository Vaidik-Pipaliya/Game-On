from django.conf import settings
from django.db import models
from django.utils import timezone

EXPIRING_DAYS = 14  # a membership with this many days or fewer left is "expiring"


class Plan(models.Model):
    """One row per plan (Gold, Silver, Junior). Benefits live here so the owner can edit them in admin."""

    name = models.CharField(max_length=20, unique=True)
    price_paise = models.PositiveIntegerField(help_text="Membership fee for one period")
    duration_days = models.PositiveIntegerField(default=365)
    court_discount_pct = models.PositiveSmallIntegerField(default=0)
    free_court_hours_per_month = models.PositiveSmallIntegerField(default=0)
    shop_discount_pct = models.PositiveSmallIntegerField(default=0)
    bar_discount_pct = models.PositiveSmallIntegerField(default=0)
    daily_booking_limit = models.PositiveSmallIntegerField(default=2)
    junior_only = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class Member(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=15, unique=True)
    email = models.EmailField(blank=True)
    date_of_birth = models.DateField()
    emergency_contact = models.CharField(max_length=120, blank=True)
    # Junior members (<18) must point at an adult member.
    guardian = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="wards")
    whatsapp_opt_in = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def current_membership(self):
        """Latest non-cancelled membership. Works with prefetch_related("memberships__plan") to avoid extra queries."""
        live = [m for m in self.memberships.all() if not m.cancelled]
        return max(live, key=lambda m: m.end_date, default=None)

    def __str__(self):
        return f"{self.full_name} ({self.phone})"


class Membership(models.Model):
    """A member's period on a plan. Status (active/expiring/expired) is computed from end_date, never stored."""

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="memberships")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    start_date = models.DateField()
    end_date = models.DateField()
    cancelled = models.BooleanField(default=False)
    reminder_sent_on = models.DateField(null=True, blank=True)

    def status_on(self, day):
        if self.cancelled:
            return "cancelled"
        if self.end_date < day:
            return "expired"
        if (self.end_date - day).days <= EXPIRING_DAYS:
            return "expiring"
        return "active"

    @property
    def status(self):
        return self.status_on(timezone.localdate())

    @property
    def days_left(self):
        return (self.end_date - timezone.localdate()).days

    def __str__(self):
        return f"{self.member.full_name} - {self.plan} until {self.end_date}"
