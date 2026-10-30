from django.db import models
from django.utils.translation import gettext_lazy as _

from api.mie.enums import StudioReviewKind
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class StudioReviewReceipt(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One accepted Studio delivery (`studio.review.v1`) of a course's text
    or video.

    Written in the same transaction as the course push it ran, so a receipt
    exists only for content the platform took. A retry of the same revision
    with the same content_hash is answered from this row instead of pushing
    again (studio_review_service.deliver).
    """

    developer = models.ForeignKey(
        "mie.DeveloperAccount",
        verbose_name=_("Developer"),
        on_delete=models.CASCADE,
        related_name="studio_review_receipts",
        help_text=_("Account that delivered the content."),
    )
    submission = models.ForeignKey(
        "mie.CourseSubmission",
        verbose_name=_("Submission"),
        on_delete=models.CASCADE,
        related_name="studio_review_receipts",
        help_text=_("The approved idea the content is for."),
    )
    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="studio_review_receipts",
        help_text=_("The course the delivery created or updated."),
    )
    schema_version = models.CharField(
        verbose_name=_("Schema Version"),
        max_length=40,
        help_text=_("Envelope version the delivery was sent as."),
    )
    kind = models.CharField(
        verbose_name=_("Kind"),
        max_length=10,
        choices=StudioReviewKind.choices,
        help_text=_("Whether the delivery carried the course text or its video."),
    )
    revision = models.PositiveIntegerField(
        verbose_name=_("Revision"),
        help_text=_("Studio's revision number, counted per idea and kind."),
    )
    content_hash = models.CharField(
        verbose_name=_("Content Hash"),
        max_length=64,
        help_text=_("SHA-256 of the canonical content, as checked on arrival."),
    )
    source_map = models.JSONField(
        verbose_name=_("Source Map"),
        default=list,
        blank=True,
        help_text=_("Studio's module and lesson ids keyed by our orders, as sent."),
    )
    media = models.JSONField(
        verbose_name=_("Media"),
        default=list,
        blank=True,
        help_text=_(
            "Each media URL of a video delivery and whether it was verified "
            "in platform storage. Empty for text."
        ),
    )
    course_status = models.CharField(
        verbose_name=_("Course Status"),
        max_length=40,
        help_text=_("The course's status right after the delivery."),
    )

    class Meta:
        verbose_name = _("Studio Review Receipt")
        verbose_name_plural = _("Studio Review Receipts")
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "kind", "revision"],
                name="mie_studio_receipt_per_revision",
            ),
        ]

    def __str__(self):
        return f"{self.kind} r{self.revision} for {self.submission_id}"
