from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from accounts.forms import BootstrapFormMixin
from members.services import find_member_by_phone

from .models import Invoice, Source


class InvoiceForm(BootstrapFormMixin, forms.Form):
    customer_name = forms.CharField(max_length=120, label="Customer or company")
    customer_gstin = forms.CharField(max_length=15, required=False, label="Customer GSTIN (optional)")
    member_phone = forms.CharField(required=False, label="Member's phone (optional)")
    description = forms.CharField(max_length=200, help_text="e.g. Gold membership, 12 months")
    source = forms.ChoiceField(choices=Source.choices, initial=Source.MEMBERSHIP, label="Revenue type")
    # Typed in rupees with paise, converted once to integer paise in the view.
    amount_rupees = forms.DecimalField(min_value=Decimal("0.01"), max_digits=10, decimal_places=2, label="Amount before GST (₹)")
    gst_pct = forms.TypedChoiceField(choices=[(r, f"{r}%") for r in Invoice.GST_RATES], coerce=int, label="GST rate")
    issued_on = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def clean_member_phone(self):
        phone = self.cleaned_data["member_phone"].strip()
        if not phone:
            return None
        member = find_member_by_phone(phone)
        if member is None:
            raise ValidationError("No member has this phone number.")
        return member

    def clean_customer_gstin(self):
        gstin = self.cleaned_data["customer_gstin"].strip().upper()
        if gstin and len(gstin) != 15:
            raise ValidationError("A GSTIN has 15 characters.")
        return gstin
