from django.db import models

from members.models import Member


class Source(models.TextChoices):
    COURT = "court"
    SHOP = "shop"
    BAR = "bar"
    MEMBERSHIP = "membership"


class Method(models.TextChoices):
    CASH = "cash"
    CARD = "card"
    UPI = "upi"
    ONLINE = "online"


class Payment(models.Model):
    """An online (Razorpay) payment attempt. The unique gateway id makes webhook replays harmless."""

    razorpay_payment_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    razorpay_order_id = models.CharField(max_length=64, blank=True)
    source = models.CharField(max_length=12, choices=Source.choices)
    amount_paise = models.PositiveIntegerField()
    status = models.CharField(max_length=10, default="created")
    created_at = models.DateTimeField(auto_now_add=True)


class Ledger(models.Model):
    """Append-only money log. Every payment, refund and expense is one row; the dashboard sums only this table."""

    class Kind(models.TextChoices):
        PAYMENT = "payment"
        REFUND = "refund"
        EXPENSE = "expense"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    source = models.CharField(max_length=12, choices=Source.choices, blank=True)
    method = models.CharField(max_length=10, choices=Method.choices, blank=True)
    # Signed: payments are positive, refunds and expenses are negative. Rows are never edited or deleted.
    amount_paise = models.IntegerField()
    note = models.CharField(max_length=200, blank=True)
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class Invoice(models.Model):
    number = models.CharField(max_length=20, unique=True)
    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.PROTECT)
    customer_name = models.CharField(max_length=120)
    description = models.CharField(max_length=200)
    amount_paise = models.PositiveIntegerField(help_text="Amount before GST")
    gst_pct = models.PositiveSmallIntegerField(default=18)
    is_paid = models.BooleanField(default=False)
    issued_on = models.DateField()
