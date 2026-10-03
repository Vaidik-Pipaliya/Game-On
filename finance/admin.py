from django.contrib import admin

from .models import Invoice, Ledger, Payment


class ReadOnlyAdmin(admin.ModelAdmin):
    """Money rows are created only by finance.services; admin can look but not touch."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Ledger)
class LedgerAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "kind", "source", "method", "amount_paise", "reference_id", "note")
    list_filter = ("kind", "source", "method")


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "razorpay_order_id", "razorpay_payment_id", "source", "amount_paise", "status")


admin.site.register(Invoice)
