import hmac
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.permissions import role_required
from members.services import send_renewal_reminders

from .models import Notification
from .services import MAX_ATTEMPTS, deliver, retry_failed, send_booking_reminders, whatsapp_configured

desk_only = role_required("owner", "front_desk")


@desk_only
def log(request):
    status = request.GET.get("status", "")
    rows = Notification.objects.select_related("member", "booking__court").order_by("-created_at")
    if status in Notification.Status.values:
        rows = rows.filter(status=status)
    return render(request, "notifications/log.html", {
        "rows": rows[:100], "status": status, "statuses": Notification.Status.choices,
        "failed": Notification.objects.filter(status=Notification.Status.FAILED).count(),
        "whatsapp_on": whatsapp_configured(), "max_attempts": MAX_ATTEMPTS,
    })


@desk_only
@require_POST
def retry(request, pk=None):
    if pk is None:
        messages.success(request, f"Retried failed messages: {retry_failed()} sent.")
    else:
        notification = deliver(get_object_or_404(Notification, pk=pk, status=Notification.Status.FAILED))
        if notification.status == Notification.Status.SENT:
            messages.success(request, "Message sent.")
        else:
            messages.error(request, f"Still failing: {notification.error}")
    return redirect("notification_log")


# ---- Scheduled jobs, called by Vercel Cron ----

def _run_booking_reminders(request):
    try:
        hours = min(max(int(request.GET.get("window_hours", 2)), 1), 24)
    except ValueError:
        hours = 2
    return send_booking_reminders(window=timedelta(hours=hours))


CRON_JOBS = {
    "booking-reminders": _run_booking_reminders,
    "retry-notifications": lambda request: retry_failed(),
    "renewal-reminders": lambda request: send_renewal_reminders(),
}


@csrf_exempt  # called by Vercel's scheduler, not a browser; the secret below is the protection
def cron(request, job):
    """Vercel Cron sends `Authorization: Bearer <CRON_SECRET>`. No secret configured = jobs disabled."""
    expected = f"Bearer {settings.CRON_SECRET}"
    given = request.headers.get("Authorization", "")
    if not settings.CRON_SECRET or not hmac.compare_digest(given, expected):
        return JsonResponse({"error": "Not allowed"}, status=403)
    if job not in CRON_JOBS:
        return JsonResponse({"error": "Unknown job"}, status=404)
    return JsonResponse({"job": job, "result": CRON_JOBS[job](request)})
