from django.contrib import admin

from api.mie.models import (
    CourseSubmission,
    DeveloperAccount,
    StudioDeliveryReceipt,
    SubmissionRejectionReason,
    WebhookEndpoint,
    WebhookEvent,
)
from api.mie.services import studio_receipt_sender


class WebhookEndpointInline(admin.TabularInline):
    model = WebhookEndpoint
    extra = 0
    fields = ("url", "all_events", "event_types", "is_deleted", "created_datetime")
    readonly_fields = ("created_datetime",)


@admin.register(DeveloperAccount)
class DeveloperAccountAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "status",
        "plan_type",
        "api_key_prefix",
        "decided_at",
        "created_datetime",
    )
    list_filter = ("status", "plan_type")
    search_fields = ("email",)
    inlines = [WebhookEndpointInline]


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
        "endpoint",
        "delivery_status",
        "attempts",
        "last_response_code",
        "delivered_at",
    )
    list_filter = ("event_type", "delivery_status")
    search_fields = (
        "submission__title",
        "submission__developer__email",
        "endpoint__url",
    )
    list_select_related = ("submission", "endpoint")


@admin.register(StudioDeliveryReceipt)
class StudioDeliveryReceiptAdmin(admin.ModelAdmin):
    """The reconciliation queue: HELD receipts need a person to look at them."""

    list_display = (
        "kind",
        "delivery_id",
        "submission",
        "status",
        "attempts",
        "last_response_code",
        "delivered_at",
        "created_datetime",
    )
    list_filter = ("status", "kind")
    search_fields = ("delivery_id", "submission__title")
    list_select_related = ("submission",)
    readonly_fields = [field.name for field in StudioDeliveryReceipt._meta.fields]
    actions = ["requeue"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Requeue held receipts (same id and payload)")
    def requeue(self, request, queryset):
        count = studio_receipt_sender.requeue(queryset)
        self.message_user(request, f"{count} receipt(s) requeued.")
