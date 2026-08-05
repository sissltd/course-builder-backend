from django.contrib import admin

from api.collaborators.models import CourseCollaborator


@admin.register(CourseCollaborator)
class CourseCollaboratorAdmin(admin.ModelAdmin):
    list_display = ("id", "course", "user", "role", "created_datetime")
    list_filter = ("role",)
