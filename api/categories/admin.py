from django.contrib import admin

from api.categories.models import Category


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "creator_price",
        "track_preference",
        "status",
        "created_datetime",
    )
    list_filter = ("status", "track_preference")
    search_fields = ("name",)
