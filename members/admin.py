from django.contrib import admin

from .models import Member, Membership, Plan


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price_paise", "court_discount_pct", "shop_discount_pct", "bar_discount_pct")


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "email", "guardian")
    search_fields = ("full_name", "phone")


admin.site.register(Membership)
