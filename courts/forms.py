from datetime import datetime

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.forms import BootstrapFormMixin
from finance.models import Method
from finance.services import DESK_METHODS
from members.services import find_member_by_phone, normalize_phone

from .models import Court

LOCAL_FORMAT = "%Y-%m-%dT%H:%M"  # what <input type="datetime-local"> submits


def parse_local_datetime(value):
    """'2026-10-09T19:00' typed or clicked at the club -> aware datetime in the club timezone."""
    try:
        return timezone.make_aware(datetime.strptime(value, LOCAL_FORMAT))
    except ValueError:
        raise ValidationError("Pick a valid date and time.")


class WhoForm(BootstrapFormMixin, forms.Form):
    """Who is playing: an existing member (by phone) or a walk-in guest (name + phone)."""

    member_phone = forms.CharField(required=False, label="Member's phone", help_text="Leave empty for a walk-in guest")
    guest_name = forms.CharField(required=False, max_length=120, label="Guest name")
    guest_phone = forms.CharField(required=False, label="Guest phone")

    def clean(self):
        data = super().clean()
        member_phone = data.get("member_phone", "").strip()
        if member_phone:
            try:
                member = find_member_by_phone(member_phone)
            except ValidationError as error:
                self.add_error("member_phone", error)
                return data
            if member is None:
                self.add_error("member_phone", "No member has this phone number. Clear it to book a walk-in, or register them first.")
            data["member"] = member
            return data

        data["member"] = None
        if not data.get("guest_name", "").strip():
            self.add_error("guest_name", "Enter the guest's name, or the member's phone number.")
        try:
            data["guest_phone"] = normalize_phone(data.get("guest_phone", ""))
        except ValidationError as error:
            self.add_error("guest_phone", error)
        return data


class BookingForm(WhoForm):
    court = forms.ModelChoiceField(queryset=Court.objects.filter(is_active=True), widget=forms.HiddenInput)
    start = forms.CharField(widget=forms.HiddenInput)
    payment_method = forms.ChoiceField(choices=Method.choices, initial=Method.CASH, label="Paid by",
                                       help_text="Online opens Razorpay. Free sessions ignore this.")

    def clean_start(self):
        return parse_local_datetime(self.cleaned_data["start"])


class SocialJoinForm(WhoForm):
    payment_method = forms.ChoiceField(choices=[(m.value, m.label) for m in DESK_METHODS])


class SocialCreateForm(BootstrapFormMixin, forms.Form):
    court = forms.ModelChoiceField(queryset=Court.objects.filter(is_active=True))
    start = forms.CharField(label="Start (a Friday)", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    capacity = forms.IntegerField(min_value=2, max_value=40, initial=12)
    price_rupees = forms.IntegerField(min_value=0, label="Price per player (₹)", initial=200)

    def clean_start(self):
        return parse_local_datetime(self.cleaned_data["start"])
