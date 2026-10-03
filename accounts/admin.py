from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .audit import record as audit
from .models import AuditLog, User


@admin.register(User)
class AppUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Club role", {"fields": ("role",)}),)
    list_display = ("username", "email", "role", "is_active")
    list_filter = ("role",)

    def save_model(self, request, obj, form, change):
        """Changing someone's role changes what they can do, so it is always logged."""
        if change and "role" in form.changed_data:
            before = User.objects.get(pk=obj.pk).role
            super().save_model(request, obj, form, change)
            audit(request.user, "user.role_change", obj, f"{obj.email}: {before} -> {obj.role}", before=before, after=obj.role)
        else:
            super().save_model(request, obj, form, change)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only: the audit trail can be looked at but never changed from admin."""

    list_display = ("created_at", "user", "action", "summary")
    list_filter = ("action",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
