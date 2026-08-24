from django.contrib import admin

from api.mie.models import (
    CourseSubmission,
    DeveloperAccount,
    SubmissionRejectionReason,
    WebhookEvent,
)


@admin.register(DeveloperAccount)
class DeveloperAccountAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "status",
        "plan_type",
        "api_key_prefix",
        "webhook_url",
        "decided_at",
        "created_datetime",
    )
    list_filter = ("status", "plan_type")
    search_fields = ("email",)


@admin.register(SubmissionRejectionReason)
class SubmissionRejectionReasonAdmin(admin.ModelAdmin):
    list_display = ("label", "is_active", "created_datetime")
    list_filter = ("is_active",)
    search_fields = ("label",)


@admin.register(CourseSubmission)
class CourseSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "developer",
        "status",
        "payout_bypass",
        "demand_score",
        "queued_at",
        "decided_at",
    )
    list_filter = ("status", "payout_bypass", "rejection_reason")
    search_fields = ("title", "developer__email")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_type",
        "submission",
        "delivery_status",
        "attempts",
        "last_response_code",
        "delivered_at",
    )
    list_filter = ("event_type", "delivery_status")
    search_fields = ("submission__title", "submission__developer__email")
