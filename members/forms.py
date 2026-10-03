import re

from django import forms
from django.utils import timezone

from .models import Member, Plan


class MemberForm(forms.Form):
    """Checks the *format* of the input. The Junior/guardian rules live in services.register_member."""

    full_name = forms.CharField(max_length=120)
    phone = forms.CharField(max_length=15, help_text="10-digit mobile number")
    email = forms.EmailField(required=False)
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    emergency_contact = forms.CharField(max_length=120, required=False)
    plan = forms.ModelChoiceField(queryset=Plan.objects.order_by("-price_paise"))
    guardian_phone = forms.CharField(required=False, label="Guardian's phone", help_text="Only for Junior members")
    whatsapp_opt_in = forms.BooleanField(required=False, label="Member agrees to WhatsApp messages")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-control"
            field.widget.attrs["class"] = css

    @staticmethod
    def _clean_phone_number(value):
        digits = re.sub(r"\D", "", value)
        if digits.startswith("91") and len(digits) == 12:  # allow +91 / 91 prefix
            digits = digits[2:]
        if len(digits) != 10:
            raise forms.ValidationError("Enter a 10-digit mobile number.")
        return digits

    def clean_phone(self):
        phone = self._clean_phone_number(self.cleaned_data["phone"])
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
        phone = self._clean_phone_number(value)
        guardian = Member.objects.filter(phone=phone).first()
        if guardian is None:
            raise forms.ValidationError("No member has this phone number. Register the guardian first.")
        return guardian  # cleaned value is the Member itself
