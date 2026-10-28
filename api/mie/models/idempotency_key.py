from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class IdempotencyKey(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One `Idempotency-Key` a developer sent on a POST, and what it answered.

    The row is claimed before the request runs and holds the response once it
    succeeds, so a retry with the same key gets that response back instead of
    doing the work twice. A request that fails releases its row, so the
    developer can fix the body and retry with the same key. Rows expire after
    idempotency_service.KEY_TTL and are purged by a periodic task.
    """

    developer = models.ForeignKey(
        "mie.DeveloperAccount",
        verbose_name=_("Developer"),
        on_delete=models.CASCADE,
        related_name="idempotency_keys",
        help_text=_("Account that sent the key. Keys are scoped per account."),
    )
    key = models.CharField(
        verbose_name=_("Key"),
        max_length=255,
        help_text=_("The Idempotency-Key header value, exactly as sent."),
    )
    scope = models.CharField(
        verbose_name=_("Scope"),
        max_length=255,
        help_text=_(
            "Method and path the key was first used on. The same key on "
            "another route is refused."
        ),
    )
    request_hash = models.CharField(
        verbose_name=_("Request Hash"),
        max_length=64,
        help_text=_(
            "SHA-256 of the canonical JSON body. The same key with a "
            "different body is refused."
        ),
    )
    response_status = models.PositiveSmallIntegerField(
        verbose_name=_("Response Status"),
        null=True,
        blank=True,
        help_text=_("HTTP status of the stored response; null while in flight."),
    )
    response_body = models.JSONField(
        verbose_name=_("Response Body"),
        null=True,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text=_("The response body replayed to a retry; null while in flight."),
    )
    completed_at = models.DateTimeField(
        verbose_name=_("Completed At"),
        null=True,
        blank=True,
        help_text=_("When the request succeeded; null while it is in flight."),
    )

    class Meta:
        verbose_name = _("Idempotency Key")
        verbose_name_plural = _("Idempotency Keys")
        constraints = [
            models.UniqueConstraint(
                fields=["developer", "key"], name="mie_idempotency_key_per_developer"
            ),
        ]
        indexes = [
            # The purge task deletes by age.
            models.Index(fields=["created_datetime"], name="mie_idem_created_idx"),
        ]

    def __str__(self):
        return f"{self.key} ({self.scope})"
