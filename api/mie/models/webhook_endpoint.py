from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.mixins import (
    DateHistoryModelMixin,
    SoftDeleteModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class WebhookEndpoint(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, SoftDeleteModelMixin):
    """One URL a developer receives webhooks on, and which events it gets.

    A developer keeps up to MAX_WEBHOOK_ENDPOINTS live endpoints and always
    at least one, so the push channel never silently disappears. An endpoint
    either takes every event, including types added later (`all_events`), or
    only the types listed in `event_types`. Each recorded event is fanned out
    to every live endpoint that takes it, as one WebhookEvent row per
    endpoint, so each URL keeps its own delivery and retry state.

    Deleting is soft: the row stays so past deliveries keep their endpoint,
    and the dispatcher fails anything still pending for it.
    """

    developer = models.ForeignKey(
        "mie.DeveloperAccount",
        verbose_name=_("Developer"),
        on_delete=models.CASCADE,
        related_name="webhook_endpoints",
        help_text=_("Developer account this endpoint belongs to."),
    )
    url = models.URLField(
        verbose_name=_("URL"),
        help_text=_("HTTPS URL that receives a signed POST for each event it takes."),
    )
    all_events = models.BooleanField(
        verbose_name=_("All Events"),
        default=True,
        help_text=_(
            "True to receive every event type, including any added later. "
            "When false, only the types in event_types are sent."
        ),
    )
    event_types = models.JSONField(
        verbose_name=_("Event Types"),
        default=list,
        blank=True,
        help_text=_(
            "WebhookEventType values this endpoint receives. Empty when "
            "all_events is true."
        ),
    )

    class Meta:
        verbose_name = _("Webhook Endpoint")
        verbose_name_plural = _("Webhook Endpoints")
        ordering = ["created_datetime"]
        constraints = [
            models.UniqueConstraint(
                fields=["developer", "url"],
                condition=Q(is_deleted=False),
                name="mie_hook_endpoint_live_url_uniq",
            ),
        ]
        indexes = [
            # Fan-out and the developer's own list both read live endpoints
            # by developer.
            models.Index(
                fields=["developer", "is_deleted"], name="mie_hook_endpoint_dev_idx"
            ),
        ]

    def __str__(self):
        return self.url
