from django.db import models
from django.utils.translation import gettext_lazy as _

from includes.helpers import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin, UserHistoryModelMixin


class Module(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin):
    """A top-level section of a Course (SCCS PRD Section 6.1: 4-12 per course)."""

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="modules",
        help_text=_("Course this module belongs to."),
    )
    title = models.CharField(
        verbose_name=_("Title"),
        max_length=255,
        help_text=_("Module title."),
    )
    order = models.PositiveIntegerField(
        verbose_name=_("Order"),
        help_text=_("Display order of this module within the course."),
    )

    class Meta:
        verbose_name = _("Module")
        verbose_name_plural = _("Modules")
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "order"], name="unique_module_order_per_course"
            ),
        ]
        indexes = [
            models.Index(fields=["course", "order"], name="module_course_order_idx"),
        ]

    def __str__(self):
        """Use the module title as the human-readable label."""

        return self.title
