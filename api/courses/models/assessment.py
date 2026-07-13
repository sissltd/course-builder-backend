from django.db import models
from django.utils.translation import gettext_lazy as _

from api.courses.enums import AssessmentLevel
from includes.helpers import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin, UserHistoryModelMixin


class Assessment(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin):
    """A quiz attached to exactly one of Lesson, Module, or Course.

    `questions` holds a list of objects shaped as:
    {"question": str, "options": [str, ...], "correct_index": int}
    """

    level = models.CharField(
        verbose_name=_("Level"),
        max_length=10,
        choices=AssessmentLevel.choices,
        help_text=_("Which entity level this assessment is attached to."),
    )
    lesson = models.OneToOneField(
        "courses.Lesson",
        verbose_name=_("Lesson"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assessment",
    )
    module = models.OneToOneField(
        "courses.Module",
        verbose_name=_("Module"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assessment",
    )
    course = models.OneToOneField(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="final_assessment",
    )
    title = models.CharField(
        verbose_name=_("Title"),
        max_length=255,
        help_text=_("Assessment title."),
    )
    questions = models.JSONField(
        verbose_name=_("Questions"),
        default=list,
        help_text=_("List of multiple-choice question objects."),
    )

    class Meta:
        verbose_name = _("Assessment")
        verbose_name_plural = _("Assessments")
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(lesson__isnull=False, module__isnull=True, course__isnull=True)
                    | models.Q(lesson__isnull=True, module__isnull=False, course__isnull=True)
                    | models.Q(lesson__isnull=True, module__isnull=True, course__isnull=False)
                ),
                name="assessment_exactly_one_parent",
            ),
        ]

    def __str__(self):
        """Use the assessment title as the human-readable label."""

        return self.title
