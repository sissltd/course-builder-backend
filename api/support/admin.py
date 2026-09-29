from django.contrib import admin

from api.support.models import SupportRequest


@admin.register(SupportRequest)
class SupportRequestAdmin(admin.ModelAdmin):
    list_display = ("kind", "email", "title", "status", "created_datetime")
    list_filter = ("kind", "status")
    search_fields = ("email", "title", "message")
    readonly_fields = ("created_datetime", "updated_datetime")
