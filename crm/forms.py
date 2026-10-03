from django import forms

from accounts.forms import BootstrapFormMixin
from courts.forms import parse_local_datetime
from courts.models import Court, Sport
from members.services import normalize_phone

from .models import Lead


class PublicFormMixin(BootstrapFormMixin):
    """A hidden 'website' field people never see. Spam bots fill in every field, so a value means a bot."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["website"] = forms.CharField(required=False, widget=forms.TextInput(attrs={
            "tabindex": "-1", "autocomplete": "off", "class": "d-none", "aria-hidden": "true",
        }))

    def is_bot(self):
        return bool(self.cleaned_data.get("website"))

    def clean_phone(self):
        return normalize_phone(self.cleaned_data["phone"])


class EnquiryForm(PublicFormMixin, forms.Form):
    name = forms.CharField(max_length=120)
    phone = forms.CharField()
    email = forms.EmailField(required=False)
    sport = forms.ModelChoiceField(queryset=Sport.objects.order_by("name"), required=False, empty_label="Not sure yet")
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, max_length=1000)


class TrialForm(PublicFormMixin, forms.Form):
    name = forms.CharField(max_length=120)
    phone = forms.CharField()
    email = forms.EmailField(required=False)
    court = forms.ModelChoiceField(queryset=Court.objects.filter(is_active=True).select_related("sport").order_by("sport__name", "name"))
    start = forms.CharField(label="Date and time", widget=forms.DateTimeInput(attrs={"type": "datetime-local", "step": 1800}),
                            help_text="Sessions are 1 hour and start on the hour or half hour")

    def clean_start(self):
        return parse_local_datetime(self.cleaned_data["start"])


class LeadForm(BootstrapFormMixin, forms.Form):
    """Staff logging a phone call or walk-in enquiry."""
    name = forms.CharField(max_length=120)
    phone = forms.CharField()
    email = forms.EmailField(required=False)
    sport_interest = forms.CharField(required=False, max_length=50)
    source = forms.ChoiceField(choices=[("phone", "Phone call"), ("walk-in", "Walk-in"), ("website", "Website")])
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)

    def clean_phone(self):
        return normalize_phone(self.cleaned_data["phone"])


class LeadUpdateForm(BootstrapFormMixin, forms.Form):
    status = forms.ChoiceField(choices=Lead.Status.choices)
    follow_up_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    lost_reason = forms.CharField(required=False, max_length=200)
