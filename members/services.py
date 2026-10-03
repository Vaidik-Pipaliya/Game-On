"""Member business rules. Views and forms call these; they hold no HTML or request logic."""

import re
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from finance.models import Source
from finance.services import record_payment
from notifications.services import send_logged_email

from .models import Member, Membership

ADULT_AGE = 18
REMINDER_DAYS = (14, 7, 1)


def normalize_phone(value):
    """Keep the 10 digits of an Indian mobile number; accept +91 / 91 prefixes and spaces."""
    digits = re.sub(r"\D", "", value)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) != 10:
        raise ValidationError("Enter a 10-digit mobile number.")
    return digits


def find_member_by_phone(phone):
    return Member.objects.filter(phone=normalize_phone(phone)).first()


def age_on(born, day):
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def _record_fee(membership, payment_method):
    if payment_method and membership.plan.price_paise:
        record_payment(
            source=Source.MEMBERSHIP, method=payment_method, amount_paise=membership.plan.price_paise,
            reference_id=membership.pk, note=f"{membership.plan.name} membership for {membership.member.full_name}",
        )


def register_member(*, full_name, phone, email, date_of_birth, plan, guardian=None,
                    emergency_contact="", whatsapp_opt_in=False, payment_method=None, today=None):
    """Create the member, their first membership and the fee payment together, or none of them."""
    today = today or timezone.localdate()
    is_minor = age_on(date_of_birth, today) < ADULT_AGE

    if is_minor and not plan.junior_only:
        raise ValidationError("Members under 18 must take the Junior plan.")
    if plan.junior_only and not is_minor:
        raise ValidationError("The Junior plan is only for members under 18.")
    if plan.junior_only:
        if guardian is None:
            raise ValidationError("A Junior member needs a guardian. Register the guardian first, then enter their phone number.")
        if age_on(guardian.date_of_birth, today) < ADULT_AGE:
            raise ValidationError("The guardian must be an adult.")
    else:
        guardian = None

    with transaction.atomic():
        member = Member.objects.create(
            full_name=full_name, phone=phone, email=email, date_of_birth=date_of_birth,
            emergency_contact=emergency_contact, guardian=guardian, whatsapp_opt_in=whatsapp_opt_in,
        )
        membership = Membership.objects.create(
            member=member, plan=plan, start_date=today, end_date=today + timedelta(days=plan.duration_days),
        )
        _record_fee(membership, payment_method)
    return member


def renew_membership(member, payment_method=None, today=None):
    """Extend on the same plan. An unexpired membership is extended from its end date, so no paid days are lost."""
    today = today or timezone.localdate()
    last = member.current_membership
    if last is None:
        raise ValidationError("This member has no membership to renew.")
    start = today if last.end_date < today else last.end_date + timedelta(days=1)
    with transaction.atomic():
        membership = Membership.objects.create(
            member=member, plan=last.plan, start_date=start, end_date=start + timedelta(days=last.plan.duration_days),
        )
        _record_fee(membership, payment_method)
    return membership


def search_members(query, limit=20):
    """Name or phone contains the text. Empty search lists the newest members."""
    members = Member.objects.prefetch_related("memberships__plan")
    query = query.strip()
    if query:
        members = members.filter(Q(full_name__icontains=query) | Q(phone__icontains=query)).order_by("full_name")
    else:
        members = members.order_by("-created_at")
    return members[:limit]


def send_renewal_reminders(today=None):
    """Email members whose membership ends in 14, 7 or 1 days. Returns how many emails were sent."""
    today = today or timezone.localdate()
    renewed_later = Membership.objects.filter(
        member=OuterRef("member"), cancelled=False, end_date__gt=OuterRef("end_date")
    )
    due = (
        Membership.objects.filter(cancelled=False, end_date__in=[today + timedelta(days=d) for d in REMINDER_DAYS])
        .exclude(Exists(renewed_later))  # already renewed: no reminder
        .exclude(reminder_sent_on=today)  # running the command twice must not double-email
        .exclude(member__email="")
        .select_related("member", "plan")
    )
    sent = 0
    for membership in due:
        days = (membership.end_date - today).days
        send_logged_email(
            to=membership.member.email, member=membership.member, template="renewal_reminder",
            subject=f"Your {membership.plan.name} membership ends in {days} day{'s' if days != 1 else ''}",
            body=(
                f"Hi {membership.member.full_name},\n\nYour {membership.plan.name} membership at The Champions Club "
                f"ends on {membership.end_date:%d %b %Y}. Visit the front desk to renew and keep your member rates.\n"
            ),
        )
        membership.reminder_sent_on = today
        membership.save(update_fields=["reminder_sent_on"])
        sent += 1
    return sent
