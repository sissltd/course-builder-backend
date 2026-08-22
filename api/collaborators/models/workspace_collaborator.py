from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import (
    DateHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class WorkspaceCollaborator(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One person on a creator account's overall team roster.

    Distinct from CourseCollaborator: this is the account-level
    'Collaborators' sidebar page - the creator's whole team, with a
    platform-wide role - not access to any specific course. Invites are
    email-keyed (the person may have no account yet); user is filled in
    when they accept. A course-level assignment of one of these people is
    a separate CourseCollaborator row that can be created later.

    sex/country_of_origin are demographic fields the dashboard roster
    displays per the target schema; role is the platform-wide role
    (admin/author/collaborator), NOT a course role.
    """

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        AUTHOR = "AUTHOR", "Author"
        COLLABORATOR = "COLLABORATOR", "Collaborator"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACTIVE = "ACTIVE", "Active"
        REMOVED = "REMOVED", "Removed"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Owner"),
        on_delete=models.CASCADE,
        related_name="workspace_collaborators",
        help_text=_("The workspace/creator account this person belongs to."),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("User"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workspace_memberships",
        help_text=_("The linked account once the invite is accepted; null until then."),
    )
    invited_email = models.EmailField(
        verbose_name=_("Invited Email"),
        help_text=_("Email the invite was sent to; unique per workspace."),
    )
    role = models.CharField(
        verbose_name=_("Role"),
        max_length=15,
        choices=Role.choices,
        default=Role.COLLABORATOR,
        help_text=_("Platform-wide role within this workspace."),
    )
    sex = models.CharField(
        verbose_name=_("Sex"),
        max_length=10,
        blank=True,
        default="",
        help_text=_("Optional demographic field shown on the roster."),
    )
    country_of_origin = models.CharField(
        verbose_name=_("Country of Origin"),
        max_length=100,
        blank=True,
        default="",
        help_text=_("Optional demographic field shown on the roster."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        help_text=_("Invite/membership lifecycle state."),
    )
    removed_at = models.DateTimeField(
        verbose_name=_("Removed At"),
        null=True,
        blank=True,
        help_text=_("When this person was removed from the workspace."),
    )

    class Meta:
        verbose_name = _("Workspace Collaborator")
        verbose_name_plural = _("Workspace Collaborators")
        ordering = ["-created_datetime"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "invited_email"],
                name="unique_workspace_member_per_email",
            ),
        ]
        indexes = [
            models.Index(fields=["owner"], name="workspace_collab_owner_idx"),
            models.Index(fields=["role"], name="workspace_collab_role_idx"),
        ]

    def __str__(self):
        """Summarize who is on whose workspace."""

        return f"{self.invited_email} on {self.owner_id} ({self.role})"
