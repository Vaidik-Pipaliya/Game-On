"""All money goes through here. No other module creates Ledger or Payment rows."""

import hashlib
import hmac

import razorpay
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from shop.models import Order

from .models import Ledger, Method, Payment, Source

DESK_METHODS = (Method.CASH, Method.CARD, Method.UPI)


def record_payment(*, source, method, amount_paise, reference_id=None, note="", payment=None, at=None):
    """`at` is only for importing history / demo data; normal use records the current time."""
    if amount_paise <= 0:
        raise ValueError("A payment must be a positive amount.")
    return Ledger.objects.create(
        kind=Ledger.Kind.PAYMENT, source=source, method=method, amount_paise=amount_paise,
        reference_id=reference_id, note=note, payment=payment, created_at=at or timezone.now(),
    )


def record_refund(*, source, method, amount_paise, reference_id=None, note="", at=None):
    """A refund is a new negative row; the original payment row is never touched."""
    if amount_paise <= 0:
        raise ValueError("A refund must be a positive amount.")
    return Ledger.objects.create(
        kind=Ledger.Kind.REFUND, source=source, method=method, amount_paise=-amount_paise,
        reference_id=reference_id, note=note, created_at=at or timezone.now(),
    )


def record_expense(*, amount_paise, method, note, reference_id=None, at=None):
    """Money going out (e.g. salaries). Negative, and never counted as revenue."""
    if amount_paise <= 0:
        raise ValueError("An expense must be a positive amount.")
    return Ledger.objects.create(
        kind=Ledger.Kind.EXPENSE, method=method, amount_paise=-amount_paise,
        reference_id=reference_id, note=note, created_at=at or timezone.now(),
    )


# ---- Razorpay (test mode) ----

def _razorpay_client():
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


class OnlinePaymentUnavailable(Exception):
    """Razorpay could not create an order (keys missing, network down, gateway error)."""


def start_online_payment(*, source, reference_id, amount_paise):
    """Create a Razorpay order; the browser then opens Razorpay Checkout for it."""
    if not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET):
        raise OnlinePaymentUnavailable("Online payments are not set up. Add the Razorpay test keys to .env.")
    try:
        order = _razorpay_client().order.create({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": f"{source}-{reference_id}",
            "notes": {"source": source, "reference_id": str(reference_id)},
        })
    except Exception as error:  # gateway boundary: any failure means "take payment another way"
        raise OnlinePaymentUnavailable("Razorpay is not reachable right now. Take payment at the desk or try again.") from error
    return Payment.objects.create(
        razorpay_order_id=order["id"], source=source, reference_id=reference_id, amount_paise=amount_paise,
    )


def _sign(secret, message):
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def checkout_signature_is_valid(order_id, payment_id, signature):
    """Razorpay signs "order_id|payment_id" with our key secret. Only Razorpay (or we) can produce it."""
    expected = _sign(settings.RAZORPAY_KEY_SECRET, f"{order_id}|{payment_id}".encode())
    return hmac.compare_digest(expected, signature or "")  # constant-time: leaks nothing through timing


def webhook_signature_is_valid(raw_body, signature):
    """Webhooks are signed over the exact raw request body with the webhook secret."""
    expected = _sign(settings.RAZORPAY_WEBHOOK_SECRET, raw_body)
    return hmac.compare_digest(expected, signature or "")


def mark_payment_captured(*, razorpay_order_id, razorpay_payment_id, amount_paise=None):
    """Record a successful online payment exactly once.

    Called by the checkout callback and again by the webhook, possibly at the same moment
    and possibly many times (Razorpay retries webhooks). The row lock plus the status check
    make every call after the first a no-op. Returns True only for the call that recorded it.
    """
    with transaction.atomic():
        payment = Payment.objects.select_for_update().filter(razorpay_order_id=razorpay_order_id).first()
        if payment is None or payment.status == Payment.Status.PAID:
            return False
        if amount_paise is not None and amount_paise != payment.amount_paise:
            return False  # never accept a payment for a different amount than we asked for
        payment.status = Payment.Status.PAID
        payment.razorpay_payment_id = razorpay_payment_id
        payment.paid_at = timezone.now()
        payment.save(update_fields=["status", "razorpay_payment_id", "paid_at"])
        record_payment(
            source=payment.source, method=Method.ONLINE, amount_paise=payment.amount_paise,
            reference_id=payment.reference_id, payment=payment, note=f"Razorpay {razorpay_payment_id}",
        )
        if payment.source == Source.COURT:
            # Imported here because courts.services itself imports finance.services.
            from courts.services import apply_online_payment

            apply_online_payment(payment.reference_id, payment)
        elif payment.source == Source.SHOP:
            Order.objects.filter(pk=payment.reference_id).update(is_paid=True, payment_method=Method.ONLINE)
        return True


# ---- Refunds through Razorpay ----

def refund_gateway_payment(payment_pk):
    """Ask Razorpay to return a captured online payment in full. Safe to call again: it acts only once.

    Returns True when the money has been (or already was) handed back, False when Razorpay refused or
    couldn't be reached; the error is kept on the Payment so the owner can retry from the dashboard.
    The ledger refund row is written by the caller in the same transaction as the cancellation; this
    call happens after that commits, so a gateway problem never blocks a cancellation.
    """
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment_pk)  # two retries can't both refund
        if payment.status != Payment.Status.PAID or not payment.razorpay_payment_id:
            return False
        if payment.refund_id:
            return True
        try:
            reply = _razorpay_client().payment.refund(payment.razorpay_payment_id, {
                "amount": payment.amount_paise,
                "notes": {"reason": "Cancelled", "source": payment.source, "reference_id": str(payment.reference_id)},
            })
        except Exception as error:  # gateway boundary: any failure means "try again later"
            payment.refund_error = str(error)[:300] or "Razorpay refused the refund."
            payment.save(update_fields=["refund_error"])
            return False
        payment.refund_id = reply["id"]
        payment.refunded_paise = payment.amount_paise
        payment.refund_error = ""
        payment.save(update_fields=["refund_id", "refunded_paise", "refund_error"])
        return True


PENDING_REFUND = "Waiting to be sent to Razorpay"


def request_gateway_refund(*, payment=None, source=None, reference_id=None):
    """Call this inside the transaction that cancels / refunds, for something paid online.

    It marks the payment "refund pending" right now, in that same transaction, so the owed refund can
    never be forgotten (it shows up under "refunds waiting" until it succeeds). Razorpay itself is
    asked only after the transaction commits. Cash, card and UPI payments taken at the desk have no
    Payment row, so for them this does nothing.
    """
    if payment is None:
        payment = Payment.objects.select_for_update().filter(
            source=source, reference_id=reference_id, status=Payment.Status.PAID, refund_id=""
        ).order_by("id").first()
    if payment is None or payment.pk is None:
        return None
    Payment.objects.filter(pk=payment.pk, refund_id="").update(refund_error=PENDING_REFUND)
    transaction.on_commit(lambda: refund_gateway_payment(payment.pk))
    return payment.pk


def refunds_waiting():
    return Payment.objects.filter(status=Payment.Status.PAID, refund_id="").exclude(refund_error="")


def retry_failed_refunds():
    return sum(1 for payment in refunds_waiting() if refund_gateway_payment(payment.pk))
