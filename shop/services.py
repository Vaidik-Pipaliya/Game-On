"""Shop rules. Counter sales and online orders both go through place_order(), so they share one stock."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from accounts.audit import record as audit
from finance.models import Method, Source
from finance.services import DESK_METHODS, record_payment, record_refund, request_gateway_refund
from members.pricing import price_for
from notifications.services import notify_low_stock

from .models import Order, OrderLine, Variant

DELIVERY_FEE_PAISE = 5000  # flat ₹50 delivery by club staff (assumption)


class OutOfStock(ValidationError):
    pass


def take_stock(variant, quantity):
    """Subtract stock in ONE SQL statement: UPDATE ... SET stock = stock - q WHERE id = v AND stock >= q.

    The database checks and subtracts atomically, so when two tills sell the last pair at the
    same moment, the second UPDATE matches 0 rows and that sale fails. Stock can never go negative.
    """
    updated = Variant.objects.filter(pk=variant.pk, stock__gte=quantity).update(stock=F("stock") - quantity)
    if not updated:
        left = Variant.objects.get(pk=variant.pk).stock
        raise OutOfStock(f"Only {left} left of {variant}." if left else f"{variant} is out of stock.")
    variant.refresh_from_db(fields=["stock"])


def return_stock(variant, quantity):
    Variant.objects.filter(pk=variant.pk).update(stock=F("stock") + quantity)


def restock(variant, quantity, by=None):
    if quantity <= 0:
        raise ValidationError("Enter how many items arrived.")
    with transaction.atomic():
        before = Variant.objects.select_for_update().get(pk=variant.pk).stock
        return_stock(variant, quantity)
        variant.refresh_from_db(fields=["stock"])
        audit(by, "stock.restock", variant, f"Restocked {variant}: +{quantity}", before=before, after=variant.stock)
    return variant


def _record_order_payment(order, payment_method):
    if payment_method in DESK_METHODS:
        record_payment(
            source=Source.SHOP, method=payment_method, amount_paise=order.total_paise,
            reference_id=order.pk, note=f"Shop order #{order.pk}",
        )
        order.is_paid = True
        order.payment_method = payment_method
    elif payment_method == Method.ONLINE:
        order.payment_method = Method.ONLINE  # paid when Razorpay confirms (finance.mark_payment_captured)


def place_order(*, items, channel, member=None, customer_name="", customer_phone="",
                fulfilment=Order.Fulfilment.PICKUP, delivery_address="", payment_method=None, placed_by=None):
    """Create an order from [(variant, quantity), ...]. Returns (order, variants that just became low on stock).

    Everything happens in one transaction: if any line is out of stock, no stock is taken and
    no order or payment is saved.
    """
    merged = {}
    for variant, quantity in items:
        if quantity < 1:
            raise ValidationError("Quantities must be at least 1.")
        merged[variant.pk] = (variant, merged.get(variant.pk, (variant, 0))[1] + quantity)
    if not merged:
        raise ValidationError("Add at least one item.")
    if channel == Order.Channel.ONLINE and member is None and not customer_name.strip():
        raise ValidationError("Enter the customer's name.")
    if fulfilment == Order.Fulfilment.DELIVERY and not delivery_address.strip():
        raise ValidationError("Enter a delivery address, or choose pickup.")
    if channel == Order.Channel.COUNTER and payment_method not in DESK_METHODS:
        raise ValidationError("Choose how the customer paid: cash, card or UPI.")

    membership = member.current_membership if member else None
    became_low = []
    with transaction.atomic():
        order = Order.objects.create(
            member=member, placed_by=placed_by, customer_name=customer_name.strip(),
            customer_phone=customer_phone.strip(), channel=channel, fulfilment=fulfilment,
            delivery_address=delivery_address.strip(),
            status=Order.Status.COMPLETED if channel == Order.Channel.COUNTER else Order.Status.PLACED,
        )
        total = 0
        # Same order of updates in every transaction (by id), so two orders can never deadlock.
        for pk in sorted(merged):
            variant, quantity = merged[pk]
            if not variant.product.is_active:
                raise ValidationError(f"{variant.product.name} is no longer sold.")
            take_stock(variant, quantity)
            if variant.stock <= variant.reorder_level < variant.stock + quantity:
                became_low.append(variant)  # crossed the line on this sale: alert once, not on every sale
            unit = price_for("shop", variant.product.price_paise, membership).amount_paise
            OrderLine.objects.create(order=order, variant=variant, quantity=quantity, unit_price_paise=unit)
            total += unit * quantity
        order.delivery_fee_paise = DELIVERY_FEE_PAISE if fulfilment == Order.Fulfilment.DELIVERY else 0
        order.total_paise = total + order.delivery_fee_paise
        _record_order_payment(order, payment_method)
        order.save()
        if became_low:
            # Email staff only once the sale is committed (a rolled-back sale shouldn't alert anyone).
            transaction.on_commit(lambda: notify_low_stock(became_low))
    return order, became_low


def _locked(order):
    return Order.objects.select_for_update().get(pk=order.pk)


def mark_ready(order):
    with transaction.atomic():
        order = _locked(order)
        if order.status != Order.Status.PLACED:
            raise ValidationError("Only placed orders can be marked ready.")
        order.status = Order.Status.READY
        order.save(update_fields=["status"])
    return order


def complete_order(order, payment_method=None):
    """Collected or delivered. An unpaid order must be paid at the desk now."""
    with transaction.atomic():
        order = _locked(order)
        if order.status not in (Order.Status.PLACED, Order.Status.READY):
            raise ValidationError("This order is already closed.")
        if not order.is_paid:
            if payment_method not in DESK_METHODS:
                raise ValidationError("This order is unpaid. Choose how the customer paid.")
            _record_order_payment(order, payment_method)
        order.status = Order.Status.COMPLETED
        order.save()
    return order


def cancel_order(order, by=None):
    """Put the stock back and refund anything paid, by the same method."""
    with transaction.atomic():
        order = _locked(order)  # a double-click cancels (and refunds) once
        if order.status not in (Order.Status.PLACED, Order.Status.READY):
            raise ValidationError("Only open orders can be cancelled.")
        for line in order.lines.select_related("variant"):
            return_stock(line.variant, line.quantity)
        if order.is_paid:
            record_refund(
                source=Source.SHOP, method=order.payment_method, amount_paise=order.total_paise,
                reference_id=order.pk, note=f"Cancelled shop order #{order.pk}",
            )
            if order.payment_method == Method.ONLINE:
                request_gateway_refund(source=Source.SHOP, reference_id=order.pk)
        order.status = Order.Status.CANCELLED
        order.save(update_fields=["status"])
        audit(by, "order.cancel", order, f"Cancelled shop order #{order.pk}",
              refunded_paise=order.total_paise if order.is_paid else 0, method=order.payment_method)
    return order


def low_stock_variants():
    return Variant.objects.filter(product__is_active=True, stock__lte=F("reorder_level")).select_related("product")
