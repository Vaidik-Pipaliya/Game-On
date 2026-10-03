from decimal import Decimal

from django import forms

from accounts.forms import BootstrapFormMixin

from .models import MenuItem


class MenuItemForm(BootstrapFormMixin, forms.ModelForm):
    """Staff type the price in rupees; it is stored in paise like every other amount."""

    price_rupees = forms.DecimalField(label="Price (₹)", min_value=Decimal("1"), max_digits=8, decimal_places=2)

    class Meta:
        model = MenuItem
        fields = ["name", "category", "station", "is_available", "image_url"]
        labels = {"station": "Made at", "is_available": "Available now", "image_url": "Picture link"}
        help_texts = {"category": "For example Coffee, Snacks, Cold drinks", "station": "Which ticket screen gets the order",
                      "image_url": "Optional. A direct link to a picture (.jpg or .png)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].widget.attrs["list"] = "menu-categories"
        if self.instance.pk:
            self.fields["price_rupees"].initial = Decimal(self.instance.price_paise) / 100
        self.order_fields(["name", "category", "price_rupees", "station", "image_url", "is_available"])

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = MenuItem.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("There is already a menu item with this name.")
        return name

    def save(self, commit=False):
        item = super().save(commit=False)
        item.price_paise = int(self.cleaned_data["price_rupees"] * 100)
        return item
