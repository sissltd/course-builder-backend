from django.db import models
from django.utils.translation import gettext_lazy as _

from includes.helpers import (
    DateHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
    UserHistoryModelMixin,
)


class Lesson(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin):
    """A self-contained lesson within a Module (SCCS PRD Section 6.1: 3-8 per module)."""

    module = models.ForeignKey(
        "courses.Module",
        verbose_name=_("Module"),
        on_delete=models.CASCADE,
        related_name="lessons",
        help_text=_("Module this lesson belongs to."),
    )
    title = models.CharField(
        verbose_name=_("Title"),
        max_length=255,
        help_text=_("Lesson title."),
    )
    order = models.PositiveIntegerField(
        verbose_name=_("Order"),
        help_text=_("Display order of this lesson within the module."),
    )
    script = models.TextField(
        verbose_name=_("Script"),
        blank=True,
        default="",
        help_text=_(
            "Lesson narration/content script, 500-1500 words (SCCS PRD Section 6.2)."
        ),
    )
    learning_objectives = models.JSONField(
        verbose_name=_("Learning Objectives"),
        default=list,
        blank=True,
        help_text=_("List of measurable learning objective strings, 2-5 per lesson."),
    )
    duration_minutes = models.PositiveIntegerField(
        verbose_name=_("Duration Minutes"),
        default=0,
        help_text=_("Estimated lesson duration in minutes."),
    )

    class Meta:
        verbose_name = _("Lesson")
        verbose_name_plural = _("Lessons")
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["module", "order"], name="unique_lesson_order_per_module"
            ),
        ]
        indexes = [
            models.Index(fields=["module", "order"], name="lesson_module_order_idx"),
        ]

    def __str__(self):
        """Use the lesson title as the human-readable label."""

        return self.title
