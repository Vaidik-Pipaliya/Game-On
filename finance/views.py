import json

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.permissions import role_required
from courts.models import Booking

from .models import Payment, Source
from .services import checkout_signature_is_valid, mark_payment_captured, webhook_signature_is_valid

desk_only = role_required("owner", "front_desk")


def _after_payment_url(payment):
    if payment.source == Source.COURT:
        booking = Booking.objects.filter(pk=payment.reference_id).first()
        if booking:
            return f"/desk/book/?date={timezone.localtime(booking.start).date().isoformat()}"
    return "/desk/"


@desk_only
def pay_page(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    if payment.status == Payment.Status.PAID:
        messages.info(request, "This payment is already complete.")
        return redirect(_after_payment_url(payment))
    return render(request, "finance/pay.html", {
        "payment": payment,
        "checkout": {  # only public values go to the browser; the secret never does
            "key": settings.RAZORPAY_KEY_ID,
            "order_id": payment.razorpay_order_id,
            "amount": payment.amount_paise,
            "currency": "INR",
            "name": "The Champions Club",
            "description": f"{payment.get_source_display()} #{payment.reference_id}",
        },
    })


@desk_only
@require_POST
def pay_verify(request, pk):
    """Razorpay Checkout's success callback. We trust nothing the browser sends until the signature checks out."""
    payment = get_object_or_404(Payment, pk=pk)
    payment_id = request.POST.get("razorpay_payment_id", "")
    signature = request.POST.get("razorpay_signature", "")
    if not checkout_signature_is_valid(payment.razorpay_order_id, payment_id, signature):
        return JsonResponse({"error": "Payment could not be verified. No money was recorded."}, status=400)
    mark_payment_captured(razorpay_order_id=payment.razorpay_order_id, razorpay_payment_id=payment_id)
    messages.success(request, "Payment received online.")
    return JsonResponse({"ok": True, "next": _after_payment_url(payment)})


@csrf_exempt  # Razorpay's servers can't send our CSRF token; the HMAC signature protects this endpoint instead
@require_POST
def razorpay_webhook(request):
    if not webhook_signature_is_valid(request.body, request.headers.get("X-Razorpay-Signature")):
        return HttpResponseBadRequest("Invalid signature")
    try:
        event = json.loads(request.body)
    except ValueError:
        return HttpResponseBadRequest("Invalid JSON")
    if event.get("event") == "payment.captured":
        entity = event["payload"]["payment"]["entity"]
        mark_payment_captured(
            razorpay_order_id=entity.get("order_id"), razorpay_payment_id=entity["id"], amount_paise=entity["amount"],
        )
    # Always 200 for a correctly signed event (even ones we ignore), so Razorpay stops retrying.
    return HttpResponse("ok")
