from django.core.validators import MinValueValidator
from django.db import models

from api.courses.enums import (
    DistributionChannel,
    DistributionStatus,
    PricingModel,
)
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class CourseDistribution(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """Design-aligned pricing and publication state for one sales channel.

    Marketplace publication remains queued until an integration worker records
    an external identifier. SoluDesk is marked published with the local course
    transaction because this backend currently owns that publication state.
    """

    course = models.ForeignKey(
        "courses.Course", on_delete=models.CASCADE, related_name="distribution_channels"
    )
    channel = models.CharField(max_length=10, choices=DistributionChannel.choices)
    learner_price = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(0)]
    )
    mie_suggested_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    pricing_model = models.CharField(
        max_length=15, choices=PricingModel.choices, default=PricingModel.ONE_TIME
    )
    approval_rate = models.CharField(max_length=100, blank=True, default="")
    marketplace_fee_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    promotional_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    platform_revenue_per_enrollment = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    mie_rationale = models.TextField(blank=True, default="")
    comparable_courses = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=10,
        choices=DistributionStatus.choices,
        default=DistributionStatus.DRAFT,
    )
    external_course_id = models.CharField(max_length=255, blank=True, default="")
    failure_reason = models.TextField(blank=True, default="")
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["channel"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "channel"], name="unique_course_distribution_channel"
            )
        ]
        indexes = [
            models.Index(
                fields=["channel", "status"], name="distribution_channel_status_ix"
            )
        ]

    def __str__(self):
        return f"{self.course.title} — {self.get_channel_display()}"
