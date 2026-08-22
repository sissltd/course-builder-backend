from django.contrib import admin

from api.collaborators.models import (
    CollaboratorInvite,
    CourseCollaborator,
    WorkspaceCollaborator,
)


@admin.register(CourseCollaborator)
class CourseCollaboratorAdmin(admin.ModelAdmin):
    list_display = ("id", "course", "user", "role", "created_datetime")
    list_filter = ("role",)


@admin.register(CollaboratorInvite)
class CollaboratorInviteAdmin(admin.ModelAdmin):
    list_display = ("email", "course", "role", "status", "created_datetime")
    list_filter = ("status", "role")
    search_fields = ("email", "course__title")


@admin.register(WorkspaceCollaborator)
class WorkspaceCollaboratorAdmin(admin.ModelAdmin):
    """The account-level team roster shown on the Collaborators sidebar."""

    list_display = ("invited_email", "owner", "role", "status", "created_datetime")
    list_filter = ("role", "status")
    search_fields = ("invited_email", "owner__email")
