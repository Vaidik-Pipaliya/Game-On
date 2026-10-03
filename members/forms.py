from django import forms
from django.utils import timezone

from accounts.forms import BootstrapFormMixin
from finance.services import DESK_METHODS

from .models import Member, Plan
from .services import normalize_phone


class MemberForm(BootstrapFormMixin, forms.Form):
    """Checks the *format* of the input. The Junior/guardian rules live in services.register_member."""

    full_name = forms.CharField(max_length=120)
    phone = forms.CharField(max_length=15, help_text="10-digit mobile number")
    email = forms.EmailField(required=False)
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    emergency_contact = forms.CharField(max_length=120, required=False)
    plan = forms.ModelChoiceField(queryset=Plan.objects.order_by("-price_paise"))
    guardian_phone = forms.CharField(required=False, label="Guardian's phone", help_text="Only for Junior members")
    whatsapp_opt_in = forms.BooleanField(required=False, label="Member agrees to WhatsApp messages")
    payment_method = forms.ChoiceField(choices=[(m.value, m.label) for m in DESK_METHODS], label="Fee paid by")

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        if Member.objects.filter(phone=phone).exists():
            raise forms.ValidationError("A member with this phone number already exists. Search for them instead.")
        return phone

    def clean_date_of_birth(self):
        born = self.cleaned_data["date_of_birth"]
        if born > timezone.localdate():
            raise forms.ValidationError("Date of birth cannot be in the future.")
        return born

    def clean_guardian_phone(self):
        value = self.cleaned_data["guardian_phone"]
        if not value:
            return None
        phone = normalize_phone(value)
        guardian = Member.objects.filter(phone=phone).first()
        if guardian is None:
            raise forms.ValidationError("No member has this phone number. Register the guardian first.")
        return guardian  # cleaned value is the Member itself
