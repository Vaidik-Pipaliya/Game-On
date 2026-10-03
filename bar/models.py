from django.conf import settings
from django.db import models

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
