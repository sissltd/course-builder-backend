from django.contrib import admin

from api.sissl_verification.models import SISSLConfiguration, SISSLLog


# >>>>>>>>>>>>>>>>>>>>>>>> SISSLLog Admin <<<<<<<<<<<<<<<<<<<<<<<<<<<<
@admin.register(SISSLLog)
class SISSLLogAdmin(admin.ModelAdmin):
    """
    Read-only-ish admin over the forensic log. We allow viewing + filtering
    but disable add/edit — log rows are written by the service layer and
    should not be hand-edited.
    """

    list_display = ("created_datetime", "kind", "status", "user", "latency_ms", "cost")
    list_filter = ("kind", "status", "created_datetime")
    search_fields = ("user__email", "error_message")
    readonly_fields = (
        "id",
        "user",
        "kind",
        "status",
        "request_summary",
        "response_summary",
        "latency_ms",
        "error_message",
        "cost",
        "created_datetime",
        "updated_datetime",
    )
    ordering = ("-created_datetime",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Allow viewing but not editing
        return request.method in ("GET", "HEAD")


# >>>>>>>>>>>>>>>>>>>>> SISSLConfiguration Admin <<<<<<<<<<<<<<<<<<<<<
@admin.register(SISSLConfiguration)
class SISSLConfigurationAdmin(admin.ModelAdmin):
    """
    Admin surface for the singleton. The model itself prevents creating a
    second row via the unique constraint on singleton_key; we further hide
    `add` to keep the UI clean.
    """

    list_display = (
        "singleton_key",
        "face_match_threshold",
        "flagging_floor",
        "liveness_threshold",
        "http_timeout_seconds",
        "updated_datetime",
    )
    readonly_fields = ("singleton_key", "id", "created_datetime", "updated_datetime")

    def has_add_permission(self, request):
        # Singleton — only one row should ever exist (seeded via management command)
        return not SISSLConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Deleting the singleton silently downgrades every SISSL call to env-var defaults
        return False
