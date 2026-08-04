from django.db import models
from django.utils.translation import gettext_lazy as _

from api.courses.enums import CollaboratorRole
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class CourseCollaborator(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A user granted access to help build a specific Course.

    No pending/status field: a row's mere existence means active access,
    per the "simple invite" design - invites only succeed against an
    existing User account, so there's no accept-later state to track.
    created_datetime doubles as the "Dated added" the frontend shows.
    """

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="collaborators",
        help_text=_("Course this collaborator has access to."),
    )
    user = models.ForeignKey(
        "users.User",
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="course_collaborations",
        help_text=_("The collaborating user."),
    )
    role = models.CharField(
        verbose_name=_("Role"),
        max_length=15,
        choices=CollaboratorRole.choices,
        default=CollaboratorRole.COLLABORATOR,
        help_text=_("This collaborator's access level on the course."),
    )
    invited_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Invited By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("User who invited this collaborator."),
    )

    class Meta:
        verbose_name = _("Course Collaborator")
        verbose_name_plural = _("Course Collaborators")
        ordering = ["-created_datetime"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "user"], name="unique_collaborator_per_course"
            ),
        ]

    def __str__(self):
        """Summarize which course this collaborator belongs to."""

        return f"{self.user_id} on {self.course_id} ({self.role})"
