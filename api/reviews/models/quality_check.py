from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import (
    DateHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class QualityCheckCriterion(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One item on the admin-configurable pre-submission quality checklist.

    Criteria are a reusable template grouped by wizard section ("Course
    information", "Course Outline", "Version", "Course Modules",
    "Thumbnail"), so admins can add, remove, or retire checklist items
    without a schema change. `order_index` positions the item within its
    section; is_active=False retires a criterion without deleting the
    historical CourseQualityCheck rows that reference it.
    """

    section = models.CharField(
        verbose_name=_("Section"),
        max_length=100,
        help_text=_(
            "Wizard section this criterion appears under, e.g. "
            "'Course information', 'Course Outline', 'Thumbnail'."
        ),
    )
    label = models.CharField(
        verbose_name=_("Label"),
        max_length=255,
        help_text=_("Checklist item text, e.g. 'Course title', 'Learning objectives'."),
    )
    order_index = models.PositiveIntegerField(
        verbose_name=_("Order"),
        default=0,
        help_text=_("Position within the section."),
    )
    is_active = models.BooleanField(
        verbose_name=_("Is Active"),
        default=True,
        help_text=_("False retires the criterion from new checks; history is kept."),
    )

    class Meta:
        verbose_name = _("Quality Check Criterion")
        verbose_name_plural = _("Quality Check Criteria")
        ordering = ["section", "order_index"]
        indexes = [
            models.Index(fields=["section"], name="qc_criterion_section_idx"),
        ]

    def __str__(self):
        """Label the criterion by its section and text."""

        return f"{self.section}: {self.label}"


class CourseQualityCheck(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A course's current result for one quality-check criterion.

    One row per (course, criterion), refreshed as the creator edits - a
    re-check upserts rather than appends, so this always reflects the
    course's latest state. `warning_note` carries the human-readable
    shortfall message (e.g. "Your description does not meet the minimum
    requirement") rendered on the checklist UI.
    """

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="quality_checks",
        help_text=_("Course this result belongs to."),
    )
    criterion = models.ForeignKey(
        "reviews.QualityCheckCriterion",
        verbose_name=_("Criterion"),
        on_delete=models.CASCADE,
        related_name="results",
        help_text=_("The checklist item this result is for."),
    )
    is_checked = models.BooleanField(
        verbose_name=_("Is Checked"),
        default=False,
        help_text=_("Whether the course currently satisfies this criterion."),
    )
    warning_note = models.CharField(
        verbose_name=_("Warning Note"),
        blank=True,
        default="",
        max_length=500,
        help_text=_("Shortfall message shown when the criterion is not met."),
    )
    checked_at = models.DateTimeField(
        verbose_name=_("Checked At"),
        null=True,
        blank=True,
        help_text=_("When this result was last computed."),
    )

    class Meta:
        verbose_name = _("Course Quality Check")
        verbose_name_plural = _("Course Quality Checks")
        ordering = ["criterion__section", "criterion__order_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "criterion"], name="unique_quality_check_per_criterion"
            ),
        ]
        indexes = [
            models.Index(fields=["course"], name="qc_result_course_idx"),
        ]

    def __str__(self):
        """Summarize the result for one criterion."""

        return f"{self.course_id} / {self.criterion_id}: {'OK' if self.is_checked else 'FAIL'}"
