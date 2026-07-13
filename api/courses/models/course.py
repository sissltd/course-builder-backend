from django.db import models
from django.utils.translation import gettext_lazy as _

from api.courses.enums import CourseStatus
from includes.helpers import (
    DateHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
    UserHistoryModelMixin,
)


class Course(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin):
    """A course authored by a single Course Creator (SCCS PRD Track A).

    Total duration is intentionally not denormalized here: it is computed on
    read by summing Lesson.duration_minutes, avoiding a cache-invalidation
    bug class for a field that is cheap to recompute.
    """

    creator = models.ForeignKey(
        "users.User",
        verbose_name=_("Creator"),
        on_delete=models.CASCADE,
        related_name="courses",
        help_text=_("Course Creator who owns this course."),
    )
    category = models.ForeignKey(
        "courses.Category",
        verbose_name=_("Category"),
        on_delete=models.PROTECT,
        related_name="courses",
        help_text=_("Category this course belongs to."),
    )
    title = models.CharField(
        verbose_name=_("Title"),
        max_length=255,
        help_text=_("Course title."),
    )
    description = models.TextField(
        verbose_name=_("Description"),
        help_text=_(
            "Course description including target audience, prerequisites, and outcomes."
        ),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=12,
        choices=CourseStatus.choices,
        default=CourseStatus.DRAFT,
        help_text=_("Current lifecycle status of the course."),
    )
    creator_price_snapshot = models.DecimalField(
        verbose_name=_("Creator Price Snapshot"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_(
            "Category creator_price captured at submission time. Null until submitted."
        ),
    )
    preview_video_url = models.URLField(
        verbose_name=_("Preview Video URL"),
        blank=True,
        default="",
        help_text=_(
            "1-2 minute course overview video URL, required before submission (BR-015)."
        ),
    )
    terms_accepted_at = models.DateTimeField(
        verbose_name=_("Terms Accepted At"),
        help_text=_(
            "When the creator accepted the category Terms and Conditions (BR-005)."
        ),
    )
    submitted_at = models.DateTimeField(
        verbose_name=_("Submitted At"), null=True, blank=True
    )
    approved_at = models.DateTimeField(
        verbose_name=_("Approved At"), null=True, blank=True
    )
    published_at = models.DateTimeField(
        verbose_name=_("Published At"), null=True, blank=True
    )
    rejected_at = models.DateTimeField(
        verbose_name=_("Rejected At"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("Course")
        verbose_name_plural = _("Courses")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(
                fields=["status", "submitted_at"], name="course_status_submitted_idx"
            ),
            models.Index(
                fields=["creator", "status"], name="course_creator_status_idx"
            ),
        ]

    def __str__(self):
        """Use the course title as the human-readable label."""

        return self.title
