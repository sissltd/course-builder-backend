from django.contrib import admin

from api.reviews.models import (
    CourseQualityCheck,
    QualityCheckCriterion,
    ReviewAction,
    ReviewFlag,
)


@admin.register(ReviewAction)
class ReviewActionAdmin(admin.ModelAdmin):
    """Read-only audit log of reviewer decisions (records are immutable)."""

    list_display = ("course", "reviewer", "action", "created_datetime")
    list_filter = ("action",)
    search_fields = ("course__title", "reviewer__email")
    ordering = ("-created_datetime",)
    readonly_fields = (
        "course",
        "reviewer",
        "action",
        "feedback",
        "created_datetime",
        "updated_datetime",
    )

    def has_add_permission(self, request):
        """Review actions are created only through the review API."""

        return False

    def has_change_permission(self, request, obj=None):
        """Records are immutable - view-only in admin."""

        return False


@admin.register(ReviewFlag)
class ReviewFlagAdmin(admin.ModelAdmin):
    """Review issues raised within a review round."""

    list_display = ("title", "flag_type", "review_action", "lesson", "is_resolved")
    list_filter = ("flag_type", "is_resolved")
    search_fields = ("title", "review_action__course__title")


@admin.register(QualityCheckCriterion)
class QualityCheckCriterionAdmin(admin.ModelAdmin):
    """The admin-editable pre-submission checklist template."""

    list_display = ("section", "label", "order_index", "is_active")
    list_filter = ("section", "is_active")
    search_fields = ("label",)
    ordering = ("section", "order_index")


@admin.register(CourseQualityCheck)
class CourseQualityCheckAdmin(admin.ModelAdmin):
    """Per-course results for each quality-check criterion."""

    list_display = ("course", "criterion", "is_checked", "checked_at")
    list_filter = ("is_checked", "criterion__section")
    search_fields = ("course__title",)
