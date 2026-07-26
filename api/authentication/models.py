from django.db import models
from django.utils.translation import gettext_lazy as _

from api.authentication.enums import TokenPurpose
from includes.helpers import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class EmailVerificationToken(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A one-time link token issued for signup verification or password reset.

    The raw token is never persisted - only its SHA-256 hash (token_hash), so
    a database leak does not expose valid links. Unlike a password hash, this
    uses a plain fast hash rather than make_password/check_password: the token
    itself is a cryptographically random ~43-char string (secrets.token_urlsafe),
    so brute-forcing it is infeasible regardless of hash speed, and a plain
    hash allows an O(1) indexed lookup by token_hash instead of "fetch the
    latest unused row for user+purpose, then compare".
    """

    user = models.ForeignKey(
        "users.User",
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="verification_tokens",
        help_text=_("User this token was issued for."),
    )
    purpose = models.CharField(
        verbose_name=_("Purpose"),
        max_length=32,
        choices=TokenPurpose.choices,
        help_text=_("What this token authorizes."),
    )
    token_hash = models.CharField(
        verbose_name=_("Token Hash"),
        max_length=64,
        unique=True,
        db_index=True,
        help_text=_(
            "SHA-256 hex digest of the raw token. The raw token is never stored."
        ),
    )
    expires_at = models.DateTimeField(
        verbose_name=_("Expires At"),
        help_text=_("When this token stops being valid."),
    )
    is_used = models.BooleanField(
        verbose_name=_("Is Used"),
        default=False,
        help_text=_("Whether this token has already been successfully verified."),
    )
    used_at = models.DateTimeField(verbose_name=_("Used At"), null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(
        verbose_name=_("Attempts"),
        default=0,
        help_text=_("Reserved abuse-tracking counter. Kept for forward compatibility."),
    )
    new_email = models.EmailField(
        verbose_name=_("New Email"),
        blank=True,
        default="",
        help_text=_(
            "Pending new email address, only populated for EMAIL_CHANGE-purpose "
            "tokens - applied to User.email once this token is confirmed."
        ),
    )

    class Meta:
        verbose_name = _("Email Verification Token")
        verbose_name_plural = _("Email Verification Tokens")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(
                fields=["user", "purpose", "is_used"], name="evt_user_purpose_used_idx"
            ),
        ]

    def __str__(self):
        """Summarize the token for admin/debugging readability without leaking it."""

        return f"{self.purpose} token for {self.user_id}"
