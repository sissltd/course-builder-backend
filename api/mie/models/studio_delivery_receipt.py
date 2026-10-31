from django.db import models
from django.utils.translation import gettext_lazy as _

from api.mie.enums import StudioReceiptKind, StudioReceiptStatus
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class StudioDeliveryReceipt(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One studio.receipt.v1 callback telling Studio a delivery is stored.

    Written in the same transaction as the topic, text or video it confirms
    (studio_receipt_service.record), so a rolled-back delivery never produces
    a receipt, and sent after commit by studio_receipt_sender. The row id is
    the receipt_id; it and `payload` stay fixed across retries. `payload`
    holds receipt metadata only: no lesson content, media URLs or secrets.
    """

    submission = models.ForeignKey(
        "mie.CourseSubmission",
        verbose_name=_("Submission"),
        on_delete=models.PROTECT,
        related_name="studio_delivery_receipts",
    )
    delivery_id = models.UUIDField(
        verbose_name=_("Delivery ID"),
        unique=True,
        help_text=_("Studio's job id, from X-Studio-Delivery-Id."),
    )
    kind = models.CharField(
        verbose_name=_("Kind"), max_length=5, choices=StudioReceiptKind.choices
    )
    payload = models.JSONField(
        verbose_name=_("Payload"),
        help_text=_("Exact receipt body sent to Studio, fixed at creation."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=StudioReceiptStatus.choices,
        default=StudioReceiptStatus.PENDING,
    )
    attempts = models.PositiveSmallIntegerField(verbose_name=_("Attempts"), default=0)
    last_response_code = models.PositiveSmallIntegerField(
        verbose_name=_("Last Response Code"), null=True, blank=True
    )
    last_error = models.CharField(
        verbose_name=_("Last Error"), max_length=500, blank=True
    )
    next_retry_at = models.DateTimeField(
        verbose_name=_("Next Retry At"), null=True, blank=True
    )
    delivered_at = models.DateTimeField(
        verbose_name=_("Delivered At"), null=True, blank=True
    )
    response = models.JSONField(
        verbose_name=_("Response"),
        default=dict,
        blank=True,
        help_text=_("Studio's answer to the delivered callback."),
    )

    class Meta:
        verbose_name = _("Studio Delivery Receipt")
        verbose_name_plural = _("Studio Delivery Receipts")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(
                fields=["status", "next_retry_at"], name="mie_studio_rcpt_retry_idx"
            ),
        ]

    def __str__(self):
        return f"{self.kind} receipt {self.delivery_id}"
