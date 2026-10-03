from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import role_required

from .models import Notification
from .services import MAX_ATTEMPTS, deliver, retry_failed, whatsapp_configured

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
