from django import forms
from django.core.exceptions import ValidationError

from accounts.forms import BootstrapFormMixin
from finance.models import Method
from finance.services import DESK_METHODS
from members.services import find_member_by_phone, normalize_phone

from .models import Order, Variant


class VariantChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, variant):
        return f"{variant.product.name} - {variant.size} ({variant.stock} left)"


class CounterLineForm(BootstrapFormMixin, forms.Form):
    variant = VariantChoiceField(
        queryset=Variant.objects.filter(product__is_active=True).select_related("product").order_by("product__name", "size"),
        required=False, label="Item",
    )
    quantity = forms.IntegerField(min_value=1, max_value=50, initial=1, required=False)


CounterLineFormSet = forms.formset_factory(CounterLineForm, extra=5)


class CounterSaleForm(BootstrapFormMixin, forms.Form):
    member_phone = forms.CharField(required=False, label="Member's phone", help_text="Gives the member discount")
    payment_method = forms.ChoiceField(choices=[(m.value, m.label) for m in DESK_METHODS], label="Paid by")

    def clean_member_phone(self):
        phone = self.cleaned_data["member_phone"].strip()
        if not phone:
            return None
        member = find_member_by_phone(phone)
        if member is None:
            raise ValidationError("No member has this phone number. Leave it empty for a walk-in sale.")
        return member  # cleaned value is the Member


class CheckoutForm(BootstrapFormMixin, forms.Form):
    customer_name = forms.CharField(max_length=120, label="Your name")
    customer_phone = forms.CharField(label="Phone")
    fulfilment = forms.ChoiceField(choices=Order.Fulfilment.choices, label="Collect or deliver")
    delivery_address = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False,
                                       help_text="Only for delivery (₹50 flat fee)")
    payment_method = forms.ChoiceField(
        choices=[(Method.ONLINE, "Pay online now (Razorpay)"), ("", "Pay at the club / on delivery")], required=False,
        label="Payment",
    )

    def clean_customer_phone(self):
        return normalize_phone(self.cleaned_data["customer_phone"])
