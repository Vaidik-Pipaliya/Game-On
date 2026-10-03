from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("template", "channel", "status", "member", "created_at")
    list_filter = ("status", "channel")
