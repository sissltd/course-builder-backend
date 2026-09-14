from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from api.courses.enums import CourseImportStatus
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class CourseImportJob(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A creator-owned document-import job.

    The uploaded object stays in private object storage; this row stores the
    durable file key plus the detected module/lesson structure that the
    frontend reviews before confirming into a real Draft course.
    """

    creator = models.ForeignKey(
        "users.User",
        verbose_name=_("Creator"),
        on_delete=models.CASCADE,
        related_name="course_import_jobs",
        help_text=_("Creator who owns this import job."),
    )
    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="course_import_jobs",
        help_text=_("Draft course created when this import is confirmed."),
    )
    category = models.ForeignKey(
        "catalog.Category",
        verbose_name=_("Category"),
        on_delete=models.PROTECT,
        related_name="course_import_jobs",
        help_text=_("Category selected for the eventual imported course."),
    )
    topic = models.ForeignKey(
        "catalog.Topic",
        verbose_name=_("Topic"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="course_import_jobs",
        help_text=_("Optional topic selected for the eventual imported course."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=20,
        choices=CourseImportStatus.choices,
        default=CourseImportStatus.QUEUED,
        help_text=_("Current processing state of this import job."),
    )
    progress = models.PositiveSmallIntegerField(
        verbose_name=_("Progress"),
        default=0,
        help_text=_("Approximate completion percentage, from 0 to 100."),
    )
    stage = models.CharField(
        verbose_name=_("Stage"),
        max_length=80,
        blank=True,
        default="",
        help_text=_("Human-readable processing stage for the frontend."),
    )
    file_key = models.CharField(
        verbose_name=_("File Key"),
        max_length=500,
        help_text=_("Durable private object-storage key returned by upload presign."),
    )
    filename = models.CharField(
        verbose_name=_("Filename"),
        max_length=255,
        help_text=_("Original uploaded document filename."),
    )
    content_type = models.CharField(
        verbose_name=_("Content Type"),
        max_length=120,
        help_text=_("Uploaded document MIME type."),
    )
    size = models.PositiveIntegerField(
        verbose_name=_("Size"),
        help_text=_("Uploaded document size in bytes."),
    )
    title = models.CharField(
        verbose_name=_("Title"),
        max_length=255,
        help_text=_("Course title captured at import start."),
    )
    description = models.TextField(
        verbose_name=_("Description"),
        blank=True,
        default="",
        help_text=_("Optional course description captured at import start."),
    )
    detected_structure = models.JSONField(
        verbose_name=_("Detected Structure"),
        default=dict,
        blank=True,
        help_text=_("Detected course/module/lesson tree awaiting creator review."),
    )
    warnings = models.JSONField(
        verbose_name=_("Warnings"),
        default=list,
        blank=True,
        help_text=_("Non-blocking parser warnings to show in the review UI."),
    )
    error_message = models.TextField(
        verbose_name=_("Error Message"),
        blank=True,
        default="",
        help_text=_("Terminal parser or confirmation error, if any."),
    )
    idempotency_key = models.CharField(
        verbose_name=_("Idempotency Key"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Client-generated retry key; unique per creator when supplied."),
    )
    cancel_requested = models.BooleanField(
        verbose_name=_("Cancel Requested"),
        default=False,
        help_text=_("Whether the creator asked to cancel this job."),
    )
    started_at = models.DateTimeField(
        verbose_name=_("Started At"), null=True, blank=True
    )
    completed_at = models.DateTimeField(
        verbose_name=_("Completed At"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("Course Import Job")
        verbose_name_plural = _("Course Import Jobs")
        ordering = ["-created_datetime"]
        constraints = [
            models.UniqueConstraint(
                fields=["creator", "idempotency_key"],
                condition=~Q(idempotency_key=""),
                name="unique_course_import_idempotency_key",
            )
        ]
        indexes = [
            models.Index(
                fields=["creator", "status"], name="courseimp_creator_status_idx"
            ),
        ]

    def __str__(self):
        return f"{self.filename}: {self.status}"
