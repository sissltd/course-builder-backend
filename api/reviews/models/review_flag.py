from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import (
    DateHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class ReviewFlag(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A specific flagged issue within a review round.

    Where ReviewAction.feedback is a free-form JSON blob, flags are
    first-class rows - one per issue - optionally scoped to a lesson or
    module. This is what backs the creator dashboard's "Course details"
    panel when a course is rejected or needs revision, e.g.
    title="P1 Lesson 2 - Script Length",
    system_message="306/500 words below minimum",
    reviewer_note="Extend the lesson script to resolve this issue".

    The optional lesson/module scope mirrors the schema's nullable FKs
    (SET_NULL) so deleting the offending lesson keeps the flag's history.
    is_resolved is flipped by the creator addressing the issue; a fresh
    review round can also supersede flags by leaving them resolved.
    """

    review_action = models.ForeignKey(
        "reviews.ReviewAction",
        verbose_name=_("Review Action"),
        on_delete=models.CASCADE,
        related_name="flags",
        help_text=_("The review decision this flag was raised in."),
    )
    lesson = models.ForeignKey(
        "courses.Lesson",
        verbose_name=_("Lesson"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_flags",
        help_text=_("Lesson this issue is scoped to, if any."),
    )
    module = models.ForeignKey(
        "courses.Module",
        verbose_name=_("Module"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_flags",
        help_text=_("Module this issue is scoped to, if any."),
    )
    flag_type = models.CharField(
        verbose_name=_("Flag Type"),
        max_length=50,
        help_text=_(
            "Machine-readable issue category, e.g. 'script_length', "
            "'missing_media', 'quiz_incomplete'."
        ),
    )
    title = models.CharField(
        verbose_name=_("Title"),
        max_length=255,
        help_text=_("Short headline shown in the issues list."),
    )
    system_message = models.CharField(
        verbose_name=_("System Message"),
        blank=True,
        default="",
        max_length=500,
        help_text=_("Auto-generated detail, e.g. '306/500 words below minimum'."),
    )
    reviewer_note = models.TextField(
        verbose_name=_("Reviewer Note"),
        blank=True,
        default="",
        help_text=_("Reviewer's guidance for resolving the issue."),
    )
    is_resolved = models.BooleanField(
        verbose_name=_("Is Resolved"),
        default=False,
        help_text=_("Whether the creator has addressed this issue."),
    )
    resolved_at = models.DateTimeField(
        verbose_name=_("Resolved At"),
        null=True,
        blank=True,
        help_text=_("When the issue was marked resolved."),
    )

    class Meta:
        verbose_name = _("Review Flag")
        verbose_name_plural = _("Review Flags")
        ordering = ["review_action", "created_datetime"]
        indexes = [
            models.Index(fields=["review_action"], name="review_flag_action_idx"),
            models.Index(fields=["lesson"], name="review_flag_lesson_idx"),
        ]

    def __str__(self):
        """Use the flag's headline as the label."""

        return self.title
