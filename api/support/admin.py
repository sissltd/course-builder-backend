from django.contrib import admin

from api.support.models import SupportRequest, SupportTicketMessage


class SupportTicketMessageInline(admin.TabularInline):
    model = SupportTicketMessage
    extra = 0
    fields = ("kind", "author", "body", "from_status", "to_status", "created_datetime")
    readonly_fields = fields


@admin.register(SupportRequest)
class SupportRequestAdmin(admin.ModelAdmin):
    list_display = ("kind", "email", "title", "status", "priority", "created_datetime")
    list_filter = ("kind", "status", "priority")
    search_fields = ("email", "title", "message")
    readonly_fields = ("created_datetime", "updated_datetime")
    inlines = [SupportTicketMessageInline]
