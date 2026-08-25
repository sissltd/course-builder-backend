from django.db import models
from django.utils.translation import gettext_lazy as _

from api.mie.enums import WebhookDeliveryStatus, WebhookEventType
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class WebhookEvent(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One outbound webhook notification to a developer.

    Created immediately for every submission transition - including the
    automated dedup short-circuits - and retried with backoff until
    delivered or exhausted. `event_id` is the developer-facing dedup key:
    receivers must treat repeated deliveries of the same event_id as one
    event.
    """

    submission = models.ForeignKey(
        "mie.CourseSubmission",
        verbose_name=_("Submission"),
        on_delete=models.CASCADE,
        related_name="webhook_events",
        help_text=_("Submission whose state change this event announces."),
    )
    event_type = models.CharField(
        verbose_name=_("Event Type"),
        max_length=40,
        choices=WebhookEventType.choices,
        help_text=_("What happened; maps 1:1 onto SubmissionStatus transitions."),
    )
    payload = models.JSONField(
        verbose_name=_("Payload"),
        help_text=_("Exact JSON body delivered (or to be delivered) to the dev."),
    )
    signature = models.CharField(
        verbose_name=_("HMAC Signature"),
        max_length=128,
        blank=True,
        help_text=_("HMAC-SHA256 hex digest included as X-MIE-Signature."),
    )
    delivery_status = models.CharField(
        verbose_name=_("Delivery Status"),
        max_length=10,
        choices=WebhookDeliveryStatus.choices,
        default=WebhookDeliveryStatus.PENDING,
    )
    attempts = models.PositiveSmallIntegerField(
        verbose_name=_("Attempts"),
        default=0,
        help_text=_("Delivery attempts made so far."),
    )
    last_response_code = models.PositiveSmallIntegerField(
        verbose_name=_("Last Response Code"),
        null=True,
        blank=True,
        help_text=_("HTTP status returned by the developer's endpoint."),
    )
    last_error = models.CharField(
        verbose_name=_("Last Error"),
        max_length=500,
        blank=True,
        help_text=_("Transport or timeout error from the latest attempt."),
    )
    next_retry_at = models.DateTimeField(
        verbose_name=_("Next Retry At"),
        null=True,
        blank=True,
        help_text=_("Earliest moment the dispatcher may try again."),
    )
    delivered_at = models.DateTimeField(
        verbose_name=_("Delivered At"),
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = _("Webhook Event")
        verbose_name_plural = _("Webhook Events")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(
                fields=["delivery_status", "next_retry_at"],
                name="mie_hook_retry_idx",
            ),
            models.Index(fields=["submission", "-created_datetime"], name="mie_hook_sub_idx"),
        ]

    def __str__(self):
        return f"{self.event_type} -> {self.submission_id}"
