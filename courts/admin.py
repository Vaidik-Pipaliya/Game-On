from django.contrib import admin

from .models import Booking, Court, SocialSession, Sport


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("court", "start", "member", "guest_name", "status", "price_paise")
    list_filter = ("status", "court")


admin.site.register([Sport, Court, SocialSession])
