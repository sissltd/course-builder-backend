from django.contrib import admin

from api.courses.models import (
    Assessment,
    CategoryRequest,
    Course,
    Lesson,
    Module,
    ReviewAction,
    Topic,
    TopicReservationRequest,
)
from api.courses.services import topic_service


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "creator", "category", "status", "created_datetime")
    list_filter = ("status", "category")
    search_fields = ("title", "creator__email")


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "course", "order")
    list_filter = ("course",)


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "module", "order", "duration_minutes")


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "level", "lesson", "module", "course")
    list_filter = ("level",)


@admin.register(ReviewAction)
class ReviewActionAdmin(admin.ModelAdmin):
    list_display = ("id", "course", "reviewer", "action", "created_datetime")
    list_filter = ("action",)


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
        "category",
        "creator_price",
        "status",
        "reserved_by",
        "reserved_until",
        "created_datetime",
    )
    list_filter = ("status", "category")
    search_fields = ("name",)

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
    search_fields = ("topic__name", "requested_by__email")
