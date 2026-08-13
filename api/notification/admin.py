from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "receiver",
        "type",
        "title",
        "content",
        "content_type",
        "is_read",
    )
    list_filter = ("type", "is_read")
    search_fields = ("receiver__email",)
