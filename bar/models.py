from django.conf import settings
from django.db import models
from django.db.models import Q

from members.models import Member


class MenuItem(models.Model):
    class Station(models.TextChoices):
        KITCHEN = "kitchen"
        BAR = "bar"

    name = models.CharField(max_length=80)
    category = models.CharField(max_length=40)
    price_paise = models.PositiveIntegerField()
    station = models.CharField(max_length=10, choices=Station.choices)
    is_available = models.BooleanField(default=True)
    # A picture link. Optional: with no link the menu shows a category icon.
    image_url = models.URLField(blank=True, default="", db_default="")

    def __str__(self):
        return self.name


class Table(models.Model):
    label = models.CharField(max_length=20, unique=True)

    def __str__(self):
        return self.label


class Tab(models.Model):
    """An open bill. A table is 'occupied' simply when it has an open Tab."""

    class Status(models.TextChoices):
        OPEN = "open"
        PAID = "paid"
        VOID = "void"

    table = models.ForeignKey(Table, null=True, blank=True, on_delete=models.PROTECT)
    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.PROTECT)
    customer_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    # Frozen when the tab is settled, for the day report.
    discount_paise = models.PositiveIntegerField(default=0)
    total_paise = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            # The database refuses a second open tab on the same table (two waiters, one table).
            models.UniqueConstraint(fields=["table"], condition=Q(status="open"), name="one_open_tab_per_table"),
        ]

    @property
    def who(self):
        if self.member:
            return self.member.full_name
        return self.customer_name or (f"Table {self.table.label}" if self.table else "Walk-in")


class TabLine(models.Model):
    class Progress(models.TextChoices):
        NEW = "new"
        READY = "ready"
        SERVED = "served"

    tab = models.ForeignKey(Tab, on_delete=models.CASCADE, related_name="lines")
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    unit_price_paise = models.PositiveIntegerField(help_text="Menu price frozen at order time")
    progress = models.CharField(max_length=10, choices=Progress.choices, default=Progress.NEW)
    ordered_at = models.DateTimeField(auto_now_add=True)


class Shift(models.Model):
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    opening_cash_paise = models.PositiveIntegerField(default=0)
    closing_cash_paise = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["staff"], condition=Q(ended_at__isnull=True), name="one_open_shift_per_staff"),
        ]


class CafeOrder(models.Model):
    """An order placed and paid online, then collected at the counter.

    Tabs are pay-later (at a table or the counter); a CafeOrder is pay-first. It reaches the kitchen
    only once Razorpay has confirmed the payment.
    """

    class Status(models.TextChoices):
        AWAITING_PAYMENT = "awaiting", "Waiting for payment"
        PREPARING = "preparing", "Being prepared"
        READY = "ready", "Ready to collect"
        COLLECTED = "collected", "Collected"
        CANCELLED = "cancelled", "Cancelled"

    placed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.PROTECT)
    customer_name = models.CharField(max_length=120)
    # Frozen when the order is placed: the menu or the member's plan changing later doesn't alter what was paid.
    subtotal_paise = models.PositiveIntegerField()
    discount_paise = models.PositiveIntegerField(default=0)
    total_paise = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.AWAITING_PAYMENT)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Cafe order #{self.pk}"


class CafeOrderLine(models.Model):
    order = models.ForeignKey(CafeOrder, on_delete=models.CASCADE, related_name="lines")
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_price_paise = models.PositiveIntegerField(help_text="Menu price frozen at order time")
    is_ready = models.BooleanField(default=False)
