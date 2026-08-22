from django.contrib import admin

from api.catalog.models import Category, CategoryRequest, Topic, TopicReservationRequest
from api.catalog.services import category_service, topic_service


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "slug",
        "creator_price",
        "track_preference",
        "status",
        "created_datetime",
    )
    list_filter = ("status", "track_preference")
    search_fields = ("name", "slug")

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


@admin.register(CategoryRequest)
class CategoryRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "requested_by",
        "status",
        "resulting_category",
        "created_datetime",
    )
    list_filter = ("status",)
    search_fields = ("name", "requested_by__email")


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "slug",
        "category",
        "creator_price",
        "status",
        "reserved_by",
        "reserved_until",
        "created_datetime",
    )
    list_filter = ("status", "category")
    search_fields = ("name", "slug")

    def save_model(self, request, obj, form, change):
        """Route edits through topic_service so admin can't bypass its rules.

        A plain obj.save() here would skip update_topic()'s refresh of
        creator_price_snapshot on courses still in the review queue - the same
        business rule the API's PATCH endpoint enforces. Routing through the
        service keeps admin from silently diverging from it.
        """

        if not change:
            obj.created_by = request.user
            obj.updated_by = request.user
            obj.save()
            return

        topic_service.update_topic(
            topic=obj,
            actor=request.user,
            data={field: form.cleaned_data[field] for field in form.changed_data},
        )


@admin.register(TopicReservationRequest)
class TopicReservationRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "topic",
        "requested_by",
        "status",
        "created_datetime",
    )
    list_filter = ("status",)
    search_fields = ("requested_by__email",)
