from django.db import models
from django.utils.translation import gettext_lazy as _

from api.reviews.enums import ReviewActionType, ReviewStage
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class ReviewAction(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """An immutable record of a reviewer's approve/reject decision on a course.

    `feedback` is shaped as:
    {"summary": str, "items": [{"module_id": str, "lesson_id": str|None, "comment": str}, ...]}
    `summary` is required on reject; both fields are optional on approve.
    """

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="review_actions",
        help_text=_("Course this review action was taken on."),
    )
    reviewer = models.ForeignKey(
        "users.User",
        verbose_name=_("Reviewer"),
        on_delete=models.SET_NULL,
        null=True,
        related_name="review_actions",
        help_text=_("Reviewer who took this action."),
    )
    action = models.CharField(
        verbose_name=_("Action"),
        max_length=10,
        choices=ReviewActionType.choices,
        help_text=_("The decision made by the reviewer."),
    )
    stage = models.CharField(
        verbose_name=_("Review Stage"),
        max_length=10,
        choices=ReviewStage.choices,
        default=ReviewStage.CONTENT,
        help_text=_("Quality gate at which this decision was made."),
    )
    feedback = models.JSONField(
        verbose_name=_("Feedback"),
        default=dict,
        blank=True,
        help_text=_("Structured reviewer feedback."),
    )

    class Meta:
        verbose_name = _("Review Action")
        verbose_name_plural = _("Review Actions")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(
                fields=["course", "-created_datetime"],
                name="reviewaction_course_dt_idx",
            ),
        ]

    def __str__(self):
        """Summarize the action taken for admin/debugging readability."""

        return f"{self.action} on {self.course_id} by {self.reviewer_id}"
