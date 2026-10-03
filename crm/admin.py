from django.contrib import admin

from .models import Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "status", "follow_up_date", "assigned_to")
    list_filter = ("status",)
