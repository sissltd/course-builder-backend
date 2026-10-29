from django.contrib import admin

from core.models import KYCOutboxEvent, TransferOutboxEvent, WebhookEvent, YouverifyWebhookOutboxEvent
from shared.constants.environ import DJANGO_ENV


@admin.register(KYCOutboxEvent)
class KYCOutboxEventAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "processed", "created_datetime")
    search_fields = ("event_type",)

    def get_readonly_fields(self, request, obj=None):
        # Combines any existing readonly_fields with all model fields. Editing is disabled in production, but allowed in development for testing purposes.
        if DJANGO_ENV == "development":
            return []
        return (
            list(self.readonly_fields)
            + [field.name for field in self.model._meta.fields]
            + [field.name for field in self.model._meta.many_to_many]
        )


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "status", "created_datetime")
    search_fields = ("event_type",)
    list_filter = ("status",)

    def get_readonly_fields(self, request, obj=None):
        # Combines any existing readonly_fields with all model fields. Editing is disabled in production, but allowed in development for testing purposes.
        if DJANGO_ENV == "development":
            return []
        return (
            list(self.readonly_fields)
            + [field.name for field in self.model._meta.fields]
            + [field.name for field in self.model._meta.many_to_many]
        )


@admin.register(TransferOutboxEvent)
class TransferOutboxEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "amount",
        "reference",
        "status",
        "recipient_code",
        "transfer_code",
        "transfer_processor",
        "created_datetime",
    )
    search_fields = ("user__email", "recipient_code")
    list_filter = ("status", "transfer_processor")

    def get_readonly_fields(self, request, obj=None):
        # Combines any existing readonly_fields with all model fields. Editing is disabled in production, but allowed in development for testing purposes.
        if DJANGO_ENV == "development":
            return []
        return (
            list(self.readonly_fields)
            + [field.name for field in self.model._meta.fields]
            + [field.name for field in self.model._meta.many_to_many]
        )


@admin.register(YouverifyWebhookOutboxEvent)
class YouverifyWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "kyc_request_id", "event_type", "status", "created_datetime", "error_message", "payload")
    search_fields = ("event_type",)
    list_filter = ("status",)

    def get_readonly_fields(self, request, obj=None):
        # Combines any existing readonly_fields with all model fields. Editing is disabled in production, but allowed in development for testing purposes.
        if DJANGO_ENV == "development":
            return []
        return (
            list(self.readonly_fields)
            + [field.name for field in self.model._meta.fields]
            + [field.name for field in self.model._meta.many_to_many]
        )