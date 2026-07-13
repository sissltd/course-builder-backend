from django.contrib import admin

from api.onboarding.models import CreatorProfile


@admin.register(CreatorProfile)
class CreatorProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "video_comfort_level", "monthly_course_capacity", "onboarding_completed_at")
    list_filter = ("video_comfort_level", "monthly_course_capacity")
    search_fields = ("user__email",)
