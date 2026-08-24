from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from api.mie.enums import DeveloperAccountStatus, MiePlanType
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class DeveloperAccount(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """An external developer registered into the MIE pipeline.

    Registration is deliberately minimal - an email and a webhook_url -
    and the account starts PENDING: it can authenticate nothing until a
    superadmin approves it, which is the moment an API key is generated
    and shown exactly once. Only the SHA-256 hash and a display prefix of
    the key are ever stored; the signing secret used to HMAC our outbound
    webhooks is retrievable through /me because possession of it grants
    verification only, never impersonation.

    REJECTED is terminal; SUSPENDED freezes keys while retaining all data
    so a superadmin can restore access without losing queue history.
    """

    email = models.EmailField(
        verbose_name=_("Developer Email"),
        unique=True,
        help_text=_(
            "Registration identity. Also the login handle for platform "
            "(OTP) access to the developer surfaces."
        ),
    )
    webhook_url = models.URLField(
        verbose_name=_("Webhook URL"),
        help_text=_(
            "HTTPS endpoint that receives a signed POST for every event "
            "against this developer's submissions."
        ),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=DeveloperAccountStatus.choices,
        default=DeveloperAccountStatus.PENDING,
        help_text=_("Where this account sits in its approval lifecycle."),
    )
    plan_type = models.CharField(
        verbose_name=("Plan Type"),
        max_length=25,
        choices=MiePlanType.choices,
        default=MiePlanType.PAID_PER_SUBMISSION,
        help_text=_(
            "Payout arrangement for accepted submissions; changeable by a "
            "superadmin at any time."
        ),
    )
    api_key_prefix = models.CharField(
        verbose_name=_("API Key Prefix"),
        max_length=16,
        blank=True,
        editable=False,
        help_text=_(
            "First characters of the issued key (scb_live_...). Non-secret; "
            "used for DB lookup and masked display in /me."
        ),
    )
    api_key_hash = models.CharField(
        verbose_name=_("API Key Hash"),
        max_length=64,
        blank=True,
        editable=False,
        help_text=_("SHA-256 hex digest of the full key. Raw key is never stored."),
    )
    api_key_issued_at = models.DateTimeField(
        verbose_name=_("API Key Issued At"),
        null=True,
        blank=True,
        help_text=_("When the current key was generated (at approval or rotation)."),
    )
    api_key_last_used_at = models.DateTimeField(
        verbose_name=_("API Key Last Used At"),
        null=True,
        blank=True,
        help_text=_("Last successful key authentication."),
    )
    signing_secret = models.CharField(
        verbose_name=_("Signing Secret"),
        max_length=64,
        blank=True,
        editable=False,
        help_text=_(
            "Shared secret for HMAC-SHA256 signatures on outbound webhooks. "
            "Verification-only credential; retrievable via /me."
        ),
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Approved By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mie_developer_accounts_approved",
        help_text=_("Superadmin who last approved this account."),
    )
    decided_at = models.DateTimeField(
        verbose_name=_("Decided At"),
        null=True,
        blank=True,
        help_text=_("When the latest approve/reject/suspend decision was taken."),
    )

    class Meta:
        verbose_name = _("Developer Account")
        verbose_name_plural = _("Developer Accounts")
        ordering = ["-created_datetime"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(api_key_hash="") | models.Q(status__in=("APPROVED", "SUSPENDED")),
                name="mie_dev_key_requires_active_status",
            ),
            models.CheckConstraint(
                check=models.Q(signing_secret="") | models.Q(status__in=("APPROVED", "SUSPENDED")),
                name="mie_dev_secret_requires_active_status",
            ),
        ]

    def __str__(self):
        return f"{self.email} ({self.status})"
