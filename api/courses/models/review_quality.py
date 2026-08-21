from django.conf import settings
from django.db import models

from api.courses.enums import (
    FindingSeverity,
    MediaAssetKind,
    QualityCheckStatus,
    QualityRiskLevel,
    ReviewStage,
)
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class ReviewAssignment(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """The reviewer currently accountable for one course quality gate."""

    course = models.ForeignKey(
        "courses.Course", on_delete=models.CASCADE, related_name="review_assignments"
    )
    stage = models.CharField(max_length=10, choices=ReviewStage.choices)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="course_review_assignments",
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["course", "stage"], name="unique_course_review_stage"
            )
        ]
        indexes = [
            models.Index(
                fields=["stage", "reviewer"], name="reviewassign_stage_reviewer_ix"
            )
        ]


class QualityCheckRun(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A snapshot of automated or provider-supplied course quality evidence."""

    course = models.ForeignKey(
        "courses.Course", on_delete=models.CASCADE, related_name="quality_check_runs"
    )
    provider = models.CharField(max_length=100, default="SCCS")
    overall_score = models.PositiveSmallIntegerField(null=True, blank=True)
    risk_level = models.CharField(
        max_length=10, choices=QualityRiskLevel.choices, default=QualityRiskLevel.MEDIUM
    )
    status = models.CharField(
        max_length=10,
        choices=QualityCheckStatus.choices,
        default=QualityCheckStatus.NOT_RUN,
    )
    plagiarism_status = models.CharField(
        max_length=10,
        choices=QualityCheckStatus.choices,
        default=QualityCheckStatus.NOT_RUN,
    )
    plagiarism_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    duplicate_status = models.CharField(
        max_length=10,
        choices=QualityCheckStatus.choices,
        default=QualityCheckStatus.NOT_RUN,
    )
    duplicate_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    raw_report = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_datetime"]


class QualityFinding(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    course = models.ForeignKey(
        "courses.Course", on_delete=models.CASCADE, related_name="quality_findings"
    )
    check_run = models.ForeignKey(
        QualityCheckRun,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="findings",
    )
    module = models.ForeignKey(
        "courses.Module",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="quality_findings",
    )
    lesson = models.ForeignKey(
        "courses.Lesson",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="quality_findings",
    )
    code = models.CharField(max_length=80)
    severity = models.CharField(max_length=10, choices=FindingSeverity.choices)
    message = models.TextField()
    evidence = models.JSONField(default=dict, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["course", "severity"], name="quality_finding_course_sev_idx"
            )
        ]


class ReviewComment(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    course = models.ForeignKey(
        "courses.Course", on_delete=models.CASCADE, related_name="review_comments"
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="course_review_comments",
    )
    stage = models.CharField(max_length=10, choices=ReviewStage.choices)
    module = models.ForeignKey(
        "courses.Module",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="review_comments",
    )
    lesson = models.ForeignKey(
        "courses.Lesson",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="review_comments",
    )
    severity = models.CharField(
        max_length=10, choices=FindingSeverity.choices, default=FindingSeverity.INFO
    )
    reason_code = models.CharField(max_length=80, blank=True, default="")
    comment = models.TextField()
    resolved_at = models.DateTimeField(null=True, blank=True)


class MediaAsset(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """Media inventory and technical evidence used by QA verification."""

    course = models.ForeignKey(
        "courses.Course", on_delete=models.CASCADE, related_name="media_assets"
    )
    lesson = models.ForeignKey(
        "courses.Lesson",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="media_assets",
    )
    kind = models.CharField(max_length=20, choices=MediaAssetKind.choices)
    url = models.URLField()
    mime_type = models.CharField(max_length=100, blank=True, default="")
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    resolution = models.CharField(max_length=20, blank=True, default="")
    subtitle_url = models.URLField(blank=True, default="")
    caption_accuracy_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    audio_lufs = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    audio_video_drift_ms = models.PositiveIntegerField(null=True, blank=True)
    accessibility = models.JSONField(default=dict, blank=True)
    verification = models.JSONField(default=dict, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verified_media_assets",
    )

    class Meta:
        indexes = [
            models.Index(fields=["course", "kind"], name="media_asset_course_kind_idx")
        ]
