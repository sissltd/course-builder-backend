from django.contrib import admin

from api.categories.models import Category
from api.categories.services import category_service


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

    def save_model(self, request, obj, form, change):
        """Route edits through category_service so updated_by stays accurate."""

        if not change:
            obj.created_by = request.user
            obj.updated_by = request.user
            obj.save()
            return

        category_service.update_category(
            category=obj,
            actor=request.user,
            data={field: form.cleaned_data[field] for field in form.changed_data},
        )
