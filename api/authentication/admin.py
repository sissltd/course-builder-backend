from django.contrib import admin

from api.authentication.models import EmailVerificationToken


@admin.register(EmailVerificationToken)
class EmailVerificationTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "purpose", "is_used", "attempts", "expires_at", "created_datetime")
    list_filter = ("purpose", "is_used")
    search_fields = ("user__email",)
