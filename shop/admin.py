from django.contrib import admin

from .models import Order, OrderLine, Product, Variant


class VariantInline(admin.TabularInline):
    model = Variant
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price_paise", "is_active", "image_url")
    inlines = [VariantInline]


admin.site.register([Order, OrderLine])
