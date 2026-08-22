from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import (
    DateHistoryModelMixin,
    UserHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class LessonRequirement(
    UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin
):
    """A prerequisite/requirement line attached to a lesson.

    Companion to Lesson.learning_objectives: objectives say what the
    learner will gain, requirements say what they need beforehand. Kept as
    rows rather than a JSON list so the future MIE content ingestion can
    reference them and reviewers can flag individual ones.
    """

    lesson = models.ForeignKey(
        "courses.Lesson",
        verbose_name=_("Lesson"),
        on_delete=models.CASCADE,
        related_name="requirements",
        help_text=_("Lesson this requirement belongs to."),
    )
    text = models.CharField(
        verbose_name=_("Text"),
        max_length=500,
        help_text=_("The requirement, e.g. 'Basic Python syntax knowledge'."),
    )
    order = models.PositiveIntegerField(
        verbose_name=_("Order"),
        default=0,
        help_text=_("Display order among the lesson's requirements."),
    )

    class Meta:
        verbose_name = _("Lesson Requirement")
        verbose_name_plural = _("Lesson Requirements")
        ordering = ["order", "created_datetime"]
        indexes = [
            models.Index(fields=["lesson", "order"], name="lesson_req_order_idx"),
        ]

    def __str__(self):
        """Use truncated requirement text as the label."""

        return self.text[:50]
