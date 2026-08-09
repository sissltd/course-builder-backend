from django.db import models
from django.utils.translation import gettext_lazy as _

from api.authentication.enums import TokenPurpose
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


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


class UserSession(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One logical login session, spanning every refresh-token rotation that
    session goes through.

    ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION are both enabled
    (config/settings/jwt.py), so a session's underlying refresh token gets a
    brand new jti on every refresh - it cannot be tracked 1:1 with a single
    OutstandingToken row. Instead, this row's own id is embedded as a "sid"
    claim on the token at login (session_service.create_session), and that
    claim survives every rotation unchanged because SimpleJWT rotates jti/
    exp/iat on the *same* payload object rather than re-deriving one from
    scratch. current_jti is kept in sync with whichever refresh token is
    currently valid for this session (set at login, updated on every refresh
    via session_service.bump_last_seen), so revoke_session can blacklist the
    exact live OutstandingToken without needing to decode/guess anything.
    """

    user = models.ForeignKey(
        "users.User",
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="sessions",
        help_text=_("User this session belongs to."),
    )
    current_jti = models.CharField(
        verbose_name=_("Current JTI"),
        max_length=255,
        db_index=True,
        help_text=_(
            "jti of whichever refresh token is currently valid for this "
            "session. Updated on every rotation."
        ),
    )
    ip_address = models.GenericIPAddressField(
        verbose_name=_("IP Address"), null=True, blank=True
    )
    user_agent = models.TextField(verbose_name=_("User Agent"), blank=True, default="")
    last_seen_at = models.DateTimeField(
        verbose_name=_("Last Seen At"),
        auto_now_add=True,
        help_text=_("Bumped on login and on every token refresh."),
    )
    revoked_at = models.DateTimeField(
        verbose_name=_("Revoked At"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("User Session")
        verbose_name_plural = _("User Sessions")
        ordering = ["-last_seen_at"]

    def __str__(self):
        """Summarize for admin/debugging readability."""

        return f"Session({self.id}) for {self.user_id}"


class MFADevice(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A user's TOTP second factor - one per user.

    secret_encrypted is the base32 TOTP secret, encrypted at rest via
    shared.utils.encryption.encrypt_field (Fernet) - never stored plaintext.
    is_enabled stays False until the enrollment flow's first successful
    confirm; a device can sit unconfirmed indefinitely with no effect (login
    only checks is_enabled). last_used_window is a replay guard: it stores
    the 30-second time-step index of the most recent successfully-verified
    code, and a second submission mapping to that same step is rejected even
    though the code itself is still cryptographically valid for that window -
    without this, "one code, one use" is only true in the docs, not enforced.
    """

    user = models.OneToOneField(
        "users.User",
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="mfa_device",
        help_text=_("User this MFA device belongs to."),
    )
    secret_encrypted = models.TextField(
        verbose_name=_("Encrypted TOTP Secret"),
        help_text=_("Fernet-encrypted base32 TOTP secret. Never stored plaintext."),
    )
    is_enabled = models.BooleanField(
        verbose_name=_("Is Enabled"),
        default=False,
        help_text=_("False until the enrollment confirm step succeeds."),
    )
    enrolled_at = models.DateTimeField(
        verbose_name=_("Enrolled At"), null=True, blank=True
    )
    last_used_window = models.BigIntegerField(
        verbose_name=_("Last Used Window"),
        null=True,
        blank=True,
        help_text=_(
            "30-second time-step index of the most recently accepted code - "
            "replay guard, rejects a second submission in the same window."
        ),
    )
    failed_attempts = models.PositiveIntegerField(
        verbose_name=_("Failed Attempts"), default=0
    )

    class Meta:
        verbose_name = _("MFA Device")
        verbose_name_plural = _("MFA Devices")

    def __str__(self):
        """Use the owning user's id as the human-readable label."""

        return f"MFADevice({self.user_id})"


class MFARecoveryCode(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One single-use MFA recovery code. Generated ~10 at a time (enrollment
    confirm, or a regenerate call) and shown to the user exactly once as
    plaintext - only the Fernet-encrypted ciphertext is persisted.

    Verification decrypts every unused row for the user and compares (at
    most ~10 rows - not a timing-sensitive secret at that scale, unlike a
    password hash compared against a huge keyspace)."""

    user = models.ForeignKey(
        "users.User",
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="mfa_recovery_codes",
        help_text=_("User this recovery code belongs to."),
    )
    code_encrypted = models.TextField(
        verbose_name=_("Encrypted Code"),
        help_text=_("Fernet-encrypted recovery code. Never stored plaintext."),
    )
    used_at = models.DateTimeField(
        verbose_name=_("Used At"),
        null=True,
        blank=True,
        help_text=_("Set the first time this code is consumed; null = unused."),
    )

    class Meta:
        verbose_name = _("MFA Recovery Code")
        verbose_name_plural = _("MFA Recovery Codes")
        indexes = [
            models.Index(fields=["user", "used_at"], name="mfa_code_user_used_idx"),
        ]

    def __str__(self):
        """Use the owning user's id as the human-readable label."""

        return f"MFARecoveryCode({self.user_id})"


class MFAChallenge(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """The login-time second-factor ticket, minted when a password check
    succeeds for an account whose role requires MFA and which has an
    enabled MFADevice.

    Follows EmailVerificationToken's own established idiom - the client-
    facing value is a separate secrets.token_urlsafe string, never the raw
    row id, and only its SHA-256 hash is persisted (challenge_token_hash).
    """

    user = models.ForeignKey(
        "users.User",
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="mfa_challenges",
        help_text=_("User this challenge was issued for."),
    )
    challenge_token_hash = models.CharField(
        verbose_name=_("Challenge Token Hash"),
        max_length=64,
        unique=True,
        db_index=True,
        help_text=_("SHA-256 hex digest of the raw challenge token."),
    )
    expires_at = models.DateTimeField(verbose_name=_("Expires At"))
    consumed_at = models.DateTimeField(
        verbose_name=_("Consumed At"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("MFA Challenge")
        verbose_name_plural = _("MFA Challenges")

    def __str__(self):
        """Use the owning user's id as the human-readable label."""

        return f"MFAChallenge({self.user_id})"
