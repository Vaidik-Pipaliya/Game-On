"""Enquiries and leads. Every enquiry becomes a Lead, is assigned to a person, and that person is emailed."""

from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from accounts.models import User
from courts.services import book_court

from .models import Lead

OPEN_STATUSES = (Lead.Status.NEW, Lead.Status.CONTACTED, Lead.Status.QUOTED)
SUBMISSIONS_PER_HOUR = 5


def allow_submission(ip_address):
    """Simple rate limit for public forms: at most 5 per IP address per hour (counter in Django's cache)."""
    key = f"public-form:{ip_address}"
    cache.add(key, 0, timeout=3600)  # creates the counter only if missing, so the hour starts at the first submit
    return cache.incr(key) <= SUBMISSIONS_PER_HOUR


def pick_assignee():
    """The front-desk person or owner with the fewest open leads, so work is shared evenly."""
    return (
        User.objects.filter(is_active=True, role__in=[User.Role.FRONT_DESK, User.Role.OWNER])
        .annotate(open_leads=Count("lead", filter=Q(lead__status__in=OPEN_STATUSES)))
        .order_by("open_leads", "id")
        .first()
    )


def _email_assignee(lead):
    if lead.assigned_to and lead.assigned_to.email:
        send_mail(
            subject=f"New {lead.source} enquiry: {lead.name}",
            message=f"{lead.name} ({lead.phone}) is interested in {lead.sport_interest or 'the club'}.\n\n"
                    f"{lead.message}\n\nFollow up by {lead.follow_up_date:%d %b}.",
            from_email=None, recipient_list=[lead.assigned_to.email], fail_silently=True,
        )


def create_lead(*, name, phone, email="", sport_interest="", message="", source="website", trial_booking=None):
    lead = Lead.objects.create(
        name=name.strip(), phone=phone, email=email, sport_interest=sport_interest, message=message.strip(),
        source=source, assigned_to=pick_assignee(), follow_up_date=timezone.localdate() + timedelta(days=1),
        trial_booking=trial_booking,
    )
    # Email only after the lead is safely saved; a mail problem must never lose the enquiry.
    transaction.on_commit(lambda: _email_assignee(lead))
    return lead


def book_trial(*, name, phone, court, start, user):
    """A signed-in visitor books ONE trial session (walk-in rate, pay at the club) and becomes a lead,
    in the same transaction. The email is the verified Google email, so every trial is traceable."""
    with transaction.atomic():
        has_upcoming_trial = Lead.objects.filter(
            source="trial", trial_booking__created_by=user, trial_booking__status="confirmed",
            trial_booking__start__gt=timezone.now(),
        ).exists()
        if has_upcoming_trial:
            raise ValidationError("You already have a trial booked. Try it first, then ask about membership.")
        email = user.email
        booking = book_court(court=court, start=start, guest_name=name, guest_phone=phone, created_by=user)
        lead = create_lead(
            name=name, phone=phone, email=email, sport_interest=court.sport.name, source="trial",
            message=f"Trial booked on {court.name} at {timezone.localtime(start):%d %b %H:%M}.",
            trial_booking=booking,
        )
    return booking, lead


def update_lead(lead, *, status, follow_up_date=None, lost_reason=""):
    if status not in Lead.Status.values:
        raise ValidationError("Choose a valid status.")
    if status == Lead.Status.LOST and not lost_reason.strip():
        raise ValidationError("Say why the lead was lost, so we can learn from it.")
    lead.status = status
    lead.follow_up_date = follow_up_date if status in OPEN_STATUSES else None
    lead.lost_reason = lost_reason.strip() if status == Lead.Status.LOST else ""
    lead.save(update_fields=["status", "follow_up_date", "lost_reason"])
    return lead
