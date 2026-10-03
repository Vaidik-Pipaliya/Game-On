from datetime import date, timedelta
from itertools import groupby

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_safe
from django.utils import timezone

from accounts.permissions import role_required
from bar.models import MenuItem
from courts.models import Court
from courts.services import grid_for_day
from members.models import Plan

from .forms import EnquiryForm, LeadForm, LeadUpdateForm, TrialForm
from .models import Lead
from .services import OPEN_STATUSES, allow_submission, book_trial, create_lead, update_lead

desk_only = role_required("owner", "front_desk")


def _client_ip(request):
    return request.META.get("REMOTE_ADDR", "")


# ---- Public website (no login) ----

@require_safe
def home(request):
    return render(request, "crm/home.html", {
        "club": settings.CLUB,
        "sports": sorted({c.sport.name for c in Court.objects.filter(is_active=True).select_related("sport")}),
        "today_grid": _public_week(1)[0],
    })


@require_safe
def plans(request):
    courts = Court.objects.filter(is_active=True).select_related("sport").order_by("sport__name", "name")
    return render(request, "crm/plans.html", {"plans": Plan.objects.order_by("-price_paise"), "courts": courts})



@require_safe
def cafe(request):
    items = MenuItem.objects.order_by("category", "name")
    return render(request, "crm/cafe.html", {
        "menu": [(category, list(group)) for category, group in groupby(items, key=lambda m: m.category)],
        "discounts": Plan.objects.filter(bar_discount_pct__gt=0).order_by("-bar_discount_pct"),
    })


def _public_week(days):
    """Free / taken per court and hour for the coming days. Never exposes who booked."""
    week = []
    for offset in range(days):
        day = timezone.localdate() + timedelta(days=offset)
        starts, rows = grid_for_day(day)
        hours = [s for s in starts if s.minute == 0]
        week.append({
            "day": day,
            "hours": hours,
            "rows": [(row["court"], [c["state"] for c in row["cells"] if c["start"].minute == 0]) for row in rows],
        })
    return week


@require_safe
def availability(request):
    return render(request, "crm/availability.html", {"week": _public_week(7)})


def _public_form(request, form_class, template, on_valid, initial=None):
    form = form_class(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        if form.is_bot():
            return redirect("enquiry_thanks")  # pretend success, store nothing
        if not allow_submission(_client_ip(request)):
            form.add_error(None, "Too many requests from your network. Please call us or try again in an hour.")
        else:
            try:
                on_valid(form.cleaned_data)
            except ValidationError as error:
                form.add_error(None, error.messages)
            else:
                return redirect("enquiry_thanks")
    return render(request, template, {"form": form, "club": settings.CLUB})


def enquiry(request):
    def save(data):
        create_lead(
            name=data["name"], phone=data["phone"], email=data["email"], message=data["message"],
            sport_interest=data["sport"].name if data["sport"] else "",
        )
    return _public_form(request, EnquiryForm, "crm/enquiry.html", save)


@login_required  # a trial needs a verified Google account, so fake bookings can't block courts anonymously
def trial(request):
    if getattr(request.user, "member", None) is not None:
        messages.info(request, "You're already a member: book your court directly.")
        return redirect("portal_grid")

    def save(data):
        book_trial(name=data["name"], phone=data["phone"], court=data["court"], start=data["start"], user=request.user)

    return _public_form(request, TrialForm, "crm/trial.html", save, initial={"name": request.user.first_name})


@require_safe
def thanks(request):
    return render(request, "crm/thanks.html", {"club": settings.CLUB})


# ---- Staff: lead pipeline ----

@desk_only
def lead_list(request):
    status = request.GET.get("status", "open")
    leads = Lead.objects.select_related("assigned_to").order_by("follow_up_date", "-created_at")
    if status == "open":
        leads = leads.filter(status__in=OPEN_STATUSES)
    elif status in Lead.Status.values:
        leads = leads.filter(status=status)
    if request.GET.get("mine"):
        leads = leads.filter(assigned_to=request.user)
    form = LeadForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        lead = create_lead(name=data["name"], phone=data["phone"], email=data["email"],
                           sport_interest=data["sport_interest"], message=data["message"], source=data["source"])
        messages.success(request, f"Lead saved and assigned to {lead.assigned_to.email if lead.assigned_to else 'nobody'}.")
        return redirect("lead_list")
    return render(request, "crm/leads.html", {
        "leads": leads, "status": status, "statuses": Lead.Status.choices, "form": form,
        "today": timezone.localdate(),
    })


@desk_only
def lead_detail(request, pk):
    lead = get_object_or_404(Lead.objects.select_related("assigned_to", "trial_booking__court", "converted_member"), pk=pk)
    form = LeadUpdateForm(request.POST or None, initial={
        "status": lead.status, "follow_up_date": lead.follow_up_date, "lost_reason": lead.lost_reason,
    })
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            update_lead(lead, status=data["status"], follow_up_date=data["follow_up_date"], lost_reason=data["lost_reason"])
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            messages.success(request, "Lead updated.")
            return redirect("lead_list")
    return render(request, "crm/lead_detail.html", {"lead": lead, "form": form})
