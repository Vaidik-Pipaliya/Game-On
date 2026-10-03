from django import forms


class BootstrapFormMixin:
    """Give every widget the Bootstrap class so forms look consistent without per-field markup."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.HiddenInput):
                continue
            css = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-control"
            field.widget.attrs["class"] = css
