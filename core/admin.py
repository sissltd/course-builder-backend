from django.contrib import admin

from core.models import OutboxEvent, PaystackWebhookEvent, TransferOutboxEvent


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "processed", "created_datetime")
    search_fields = ("event_type",)


@admin.register(PaystackWebhookEvent)
class PaystackWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "status", "created_datetime")
    search_fields = ("event_type",)
    list_filter = ("status",)


@admin.register(TransferOutboxEvent)
class TransferOutboxEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "amount",
        "recipient_code",
        "status",
        "created_datetime",
    )
    search_fields = ("user__email", "recipient_code")
    list_filter = ("status",)
