from django.db import models
from django.utils import timezone

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
    ONLINE = "online", "Online (Razorpay)"


class Payment(models.Model):
    """One Razorpay order. The unique gateway payment id makes webhook replays harmless."""

    class Status(models.TextChoices):
        CREATED = "created"
        PAID = "paid"

    razorpay_order_id = models.CharField(max_length=64, unique=True)
    razorpay_payment_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    source = models.CharField(max_length=12, choices=Source.choices)
    reference_id = models.PositiveIntegerField(help_text="Id of the booking/order this pays for")
    amount_paise = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CREATED)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)


class LedgerQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise TypeError("Ledger rows are never edited. Add a reversing row instead.")

    def delete(self):
        raise TypeError("Ledger rows are never deleted. Add a reversing row instead.")


class Ledger(models.Model):
    """Append-only money log. Every payment, refund and expense is one row; the dashboard sums only this table."""

    class Kind(models.TextChoices):
        PAYMENT = "payment"
        REFUND = "refund"
        EXPENSE = "expense"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    source = models.CharField(max_length=12, choices=Source.choices, blank=True)
    method = models.CharField(max_length=10, choices=Method.choices, blank=True)
    # Signed: payments are positive, refunds and expenses are negative.
    amount_paise = models.IntegerField()
    reference_id = models.PositiveIntegerField(null=True, blank=True, help_text="Id of the booking/order/membership")
    note = models.CharField(max_length=200, blank=True)
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.PROTECT)
    # Set once when the row is created (a default, so demo data can be dated in the past); never changed after.
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    objects = LedgerQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise TypeError("Ledger rows are never edited. Add a reversing row instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError("Ledger rows are never deleted. Add a reversing row instead.")


class Invoice(models.Model):
    GST_RATES = (0, 5, 12, 18, 28)

    number = models.CharField(max_length=20, unique=True)  # CC/2026/0001, never reused
    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.PROTECT)
    customer_name = models.CharField(max_length=120)
    customer_gstin = models.CharField(max_length=15, blank=True, help_text="For business customers")
    description = models.CharField(max_length=200)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.MEMBERSHIP)
    amount_paise = models.PositiveIntegerField(help_text="Amount before GST")
    gst_pct = models.PositiveSmallIntegerField(default=18)
    is_paid = models.BooleanField(default=False)
    paid_method = models.CharField(max_length=10, blank=True)
    issued_on = models.DateField()

    @property
    def gst_paise(self):
        return (self.amount_paise * self.gst_pct + 50) // 100  # integer maths, rounded half up

    @property
    def half_rate(self):
        """CGST and SGST rate as shown on the invoice: 18 -> "9", 5 -> "2.5"."""
        return f"{self.gst_pct / 2:g}"

    @property
    def cgst_paise(self):
        """Within one state, GST is split half central (CGST), half state (SGST)."""
        return self.gst_paise // 2

    @property
    def sgst_paise(self):
        return self.gst_paise - self.cgst_paise  # the odd paisa goes here, so the halves always add up

    @property
    def total_paise(self):
        return self.amount_paise + self.gst_paise
