"""Booking messages by WhatsApp (Meta Cloud API, approved templates) with email fallback.

Rules:
- Messages are sent only after the booking is saved (transaction.on_commit), so a slow or failing
  WhatsApp call can never undo or block a booking.
- Every attempt is a Notification row (sent / failed + error), so staff can see and retry failures.
- WhatsApp only for members who opted in; otherwise (or if WhatsApp fails) email if we have one.
"""

from datetime import timedelta

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from accounts.templatetags.money import rupees
from courts.models import Booking

from .models import Notification

MAX_ATTEMPTS = 3
REMINDER_WINDOW = timedelta(hours=2)
BOOKING_TEMPLATES = ("booking_confirmed", "booking_reminder", "booking_cancelled")


def whatsapp_configured():
    return bool(settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID)


def _booking_parts(booking):
    local = timezone.localtime(booking.start)
    return booking.court.name, local.strftime("%a %d %b"), local.strftime("%I:%M %p").lstrip("0")


def _email_text(template, booking):
    court, day, time = _booking_parts(booking)
    if template == "booking_confirmed":
        return "Booking confirmed", f"Your booking is confirmed: {court} on {day} at {time}. Price {rupees(booking.price_paise)}."
    if template == "booking_reminder":
        return "Booking reminder", f"Reminder: you're playing on {court} today at {time}. See you soon!"
    return "Booking cancelled", f"Your booking for {court} on {day} at {time} has been cancelled."


def _send_whatsapp(notification):
    court, day, time = _booking_parts(notification.booking)
    response = requests.post(
        f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
        headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"},
        json={
            "messaging_product": "whatsapp",
            "to": f"91{notification.to}",
            "type": "template",
            "template": {
                "name": notification.template,
                "language": {"code": "en"},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": v} for v in (court, day, time)]}],
            },
        },
        timeout=10,
    )
    response.raise_for_status()


def deliver(notification):
    """One attempt. Records the result; on a WhatsApp failure, falls back to email once."""
    notification.attempts += 1
    try:
        if notification.channel == Notification.Channel.WHATSAPP:
            _send_whatsapp(notification)
        else:
            send_mail(notification.subject, notification.body, None, [notification.to])
    except Exception as error:  # network, HTTP 4xx/5xx, SMTP: all mean "not delivered"
        notification.status = Notification.Status.FAILED
        notification.error = str(error)[:300]
    else:
        notification.status = Notification.Status.SENT
        notification.error = ""
        notification.sent_at = timezone.now()
    notification.save()
    if notification.status == Notification.Status.FAILED and notification.channel == Notification.Channel.WHATSAPP:
        _email_fallback(notification)
    return notification


def _email_fallback(failed):
    member = failed.member
    already = Notification.objects.filter(booking=failed.booking, template=failed.template, channel=Notification.Channel.EMAIL)
    if member and member.email and not already.exists():
        _create_email(member, failed.booking, failed.template)


def _create_email(member, booking, template):
    subject, body = _email_text(template, booking)
    return deliver(Notification.objects.create(
        member=member, booking=booking, template=template, channel=Notification.Channel.EMAIL,
        to=member.email, subject=subject, body=body,
    ))


def notify_booking(booking, template):
    """Send a booking message to the member. Walk-in guests have given no consent, so they get nothing."""
    member = booking.member
    if member is None:
        return None
    if member.whatsapp_opt_in and whatsapp_configured():
        return deliver(Notification.objects.create(
            member=member, booking=booking, template=template, channel=Notification.Channel.WHATSAPP, to=member.phone,
        ))
    if member.email:
        return _create_email(member, booking, template)
    return None


def notify_booking_after_commit(booking, template):
    """Call inside the booking transaction; the message goes out only if the transaction commits."""
    transaction.on_commit(lambda: notify_booking(booking, template))


def send_booking_reminders(now=None, window=REMINDER_WINDOW):
    """Remind members about sessions starting within `window` (2 hours by default). Each booking once only.

    Run every 15 minutes where cron allows it; on Vercel's free plan (one run a day) the morning
    run uses a window covering the whole day instead.
    """
    now = now or timezone.now()
    due = Booking.objects.filter(
        status=Booking.Status.CONFIRMED, member__isnull=False, start__gt=now, start__lte=now + window,
    ).exclude(notification__template="booking_reminder").select_related("court", "member")
    return sum(1 for booking in due if notify_booking(booking, "booking_reminder"))


def retry_failed():
    """Try failed messages again (up to 3 attempts each). Used by the Retry button and a cron command."""
    failed = Notification.objects.filter(status=Notification.Status.FAILED, attempts__lt=MAX_ATTEMPTS).select_related("booking__court", "member")
    return sum(1 for n in failed if deliver(n).status == Notification.Status.SENT)


def send_logged_email(*, to, subject, body, template, member=None):
    """Email that isn't about a booking (renewal reminders, staff alerts), still logged for visibility."""
    return deliver(Notification.objects.create(
        member=member, template=template, channel=Notification.Channel.EMAIL, to=to, subject=subject, body=body,
    ))


def notify_low_stock(variants):
    """Email the owner and shop staff when items cross their reorder level."""
    staff = get_user_model().objects.filter(is_active=True, role__in=["owner", "shop_staff"]).exclude(email="")
    lines = "\n".join(f"- {v} ({v.stock} left, reorder at {v.reorder_level})" for v in variants)
    for user in staff:
        send_logged_email(to=user.email, subject="Low stock: time to reorder", template="low_stock",
                          body=f"These items are running low:\n{lines}\n")
