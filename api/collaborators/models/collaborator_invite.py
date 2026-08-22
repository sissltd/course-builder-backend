import secrets

from django.db import models
from django.utils.translation import gettext_lazy as _

from api.collaborators.enums import CollaboratorInviteStatus, CollaboratorRole
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class CollaboratorInvite(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A pending invitation to collaborate on a Course, keyed by email.

    Unlike the immediate grant it replaces, an invite exists independently
    of the invitee's account: the email may not belong to any User yet.
    The invitee accepts (or declines) while signed in with a matching
    email; acceptance is what creates the CourseCollaborator row. The
    token is opaque and single-purpose - it identifies the invite in
    notification deep-links, but every state-changing endpoint still
    requires an authenticated session, so possession of the token alone
    grants nothing.
    """

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="collaborator_invites",
        help_text=_("Course this invite grants access to when accepted."),
    )
    email = models.EmailField(
        verbose_name=_("Invitee Email"),
        help_text=_(
            "The invited person's email. May not belong to a User account "
            "yet; they must sign up with this email to accept."
        ),
    )
    role = models.CharField(
        verbose_name=_("Role"),
        max_length=15,
        choices=CollaboratorRole.choices,
        default=CollaboratorRole.COLLABORATOR,
        help_text=_("Access level granted on acceptance."),
    )
    assigned_modules = models.ManyToManyField(
        "courses.Module",
        verbose_name=("Assigned Modules"),
        related_name="invites",
        blank=True,
        help_text=_(
            "Modules the invitee will be restricted to if they accept as a "
            "plain COLLABORATOR. Ignored for ADMIN-role invites."
        ),
    )
    invited_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Invited By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="collaborator_invites_sent",
        help_text=_("User who sent this invite."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=CollaboratorInviteStatus.choices,
        default=CollaboratorInviteStatus.PENDING,
        help_text=_("Where this invite sits in its lifecycle."),
    )
    token = models.CharField(
        verbose_name=_("Token"),
        max_length=64,
        unique=True,
        editable=False,
        help_text=_("Opaque identifier used in notification deep-links."),
    )
    expires_at = models.DateTimeField(
        verbose_name=_("Expires At"),
        help_text=_("After this moment a PENDING invite can no longer be accepted."),
    )
    responded_at = models.DateTimeField(
        verbose_name=_("Responded At"),
        null=True,
        blank=True,
        help_text=_("When the invitee accepted or declined, if they did."),
    )

    class Meta:
        verbose_name = _("Collaborator Invite")
        verbose_name_plural = _("Collaborator Invites")
        ordering = ["-created_datetime"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "email"],
                condition=models.Q(status="PENDING"),
                name="unique_pending_invite_per_course_email",
            ),
            models.CheckConstraint(
                check=~models.Q(status="PENDING", responded_at__isnull=False),
                name="pending_invite_has_no_response",
            ),
        ]
        indexes = [
            models.Index(fields=["email"], name="invite_email_idx"),
        ]

    def save(self, *args, **kwargs):
        """Generate the opaque token on first save."""

        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    @property
    def invitee_user(self):
        """The User account matching this invite's email, if one exists."""

        from api.users.models import User

        return User.objects.filter(email__iexact=self.email).first()

    def __str__(self):
        """Summarize who is invited where, and the invite's status."""

        return f"{self.email} -> {self.course_id} ({self.status})"
