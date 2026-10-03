from django.conf import settings
from django.db import models

from members.models import Member


class Product(models.Model):
    class Category(models.TextChoices):
        RACKET = "racket"
        BALL = "ball"
        SHOES = "shoes"
        ACCESSORY = "accessory"
        APPAREL = "apparel"

    name = models.CharField(max_length=120)
    category = models.CharField(max_length=20, choices=Category.choices)
    price_paise = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    # A picture link (hosted anywhere). Optional: with no link the shop shows a category icon.
    image_url = models.URLField(blank=True, default="", db_default="")

    def __str__(self):
        return self.name


class Variant(models.Model):
    """The thing actually sold and counted: a product in one size (or 'One size')."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    size = models.CharField(max_length=20, default="One size")
    stock = models.PositiveIntegerField(default=0)
    reorder_level = models.PositiveIntegerField(default=3)

    class Meta:
        unique_together = [("product", "size")]

    @property
    def is_low(self):
        return self.stock <= self.reorder_level

    def __str__(self):
        return f"{self.product.name} - {self.size}"


class Order(models.Model):
    """Counter sales and online orders share this table and the same Variant.stock."""

    class Channel(models.TextChoices):
        COUNTER = "counter"
        ONLINE = "online"

    class Fulfilment(models.TextChoices):
        PICKUP = "pickup"
        DELIVERY = "delivery"

    class Status(models.TextChoices):
        PLACED = "placed"
        READY = "ready"
        COMPLETED = "completed"
        CANCELLED = "cancelled"

    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.PROTECT)
    placed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    customer_name = models.CharField(max_length=120, blank=True)
    customer_phone = models.CharField(max_length=15, blank=True)
    channel = models.CharField(max_length=10, choices=Channel.choices)
    fulfilment = models.CharField(max_length=10, choices=Fulfilment.choices, default=Fulfilment.PICKUP)
    delivery_address = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PLACED)
    delivery_fee_paise = models.PositiveIntegerField(default=0)
    total_paise = models.PositiveIntegerField(default=0, help_text="Lines after member discount + delivery fee")
    payment_method = models.CharField(max_length=10, blank=True)
    is_paid = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def who(self):
        return self.member.full_name if self.member else (self.customer_name or "Walk-in")


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_price_paise = models.PositiveIntegerField(help_text="Price after member discount, frozen at sale time")
