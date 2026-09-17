from django.contrib import admin

from api.achievements.models import Badge, CreatorBadge


@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    """Read-only: badge writes go through badge_service so backfill, audit and
    notifications are never skipped."""

    list_display = ("title", "criterion", "required_count", "auto_award", "is_deleted")
    list_filter = ("criterion", "auto_award", "is_deleted")
    search_fields = ("title",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CreatorBadge)
class CreatorBadgeAdmin(admin.ModelAdmin):
    """Read-only for the same reason as BadgeAdmin."""

    list_display = ("badge", "creator", "source", "awarded_at", "is_deleted")
    list_filter = ("source", "is_deleted")
    search_fields = ("badge__title", "creator__email")
    raw_id_fields = ("badge", "creator", "awarded_by")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
