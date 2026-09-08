from django.contrib import admin

from api.authentication.models import EmailVerificationToken, ExternalIdentity


@admin.register(EmailVerificationToken)
class EmailVerificationTokenAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "purpose",
        "is_used",
        "attempts",
        "expires_at",
        "created_datetime",
    )
    list_filter = ("purpose", "is_used")
    search_fields = ("user__email",)


@admin.register(ExternalIdentity)
class ExternalIdentityAdmin(admin.ModelAdmin):
    """Read-only provider identity bindings for security diagnostics."""

    list_display = ("id", "user", "provider", "email", "created_datetime")
    list_filter = ("provider",)
    search_fields = ("user__email", "email", "subject")
    readonly_fields = (
        "id",
        "user",
        "provider",
        "subject",
        "email",
        "created_datetime",
        "updated_datetime",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
