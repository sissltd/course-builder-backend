from django.db import models
from django.utils.translation import gettext_lazy as _

from api.courses.enums import AppealStatus
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class CourseAppeal(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A Course Creator's written dispute against a course rejection (SCCS
    PRD Section 12: "Creator disputes rejection... Creator submits written
    dispute through platform... Escalated to Senior Reviewer. Decision is
    final and logged.").

    Mirrors TopicReservationRequest's shape: the creator submits a request,
    an Admin/Super Admin approves or rejects it. Approving reopens the
    course for review (status -> SUBMITTED, see course_appeal_service.
    approve_appeal); rejecting just closes the appeal out with
    `decision_notes` - the decision is final, per the PRD wording.
    """

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="appeals",
        help_text=_("The rejected course this appeal disputes."),
    )
    submitted_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Submitted By"),
        on_delete=models.CASCADE,
        related_name="course_appeals",
        help_text=_("Course Creator who filed this appeal."),
    )
    title = models.CharField(verbose_name=_("Title"), max_length=255)
    email = models.EmailField(
        verbose_name=_("Email"),
        help_text=_("Contact email supplied on the appeal form."),
    )
    web_link = models.URLField(
        verbose_name=_("Web Link"), blank=True, help_text=_("Optional supporting link.")
    )
    description = models.TextField(verbose_name=_("Description"))
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=AppealStatus.choices,
        default=AppealStatus.PENDING,
        help_text=_("Whether this appeal is pending, approved, or rejected."),
    )
    decision_notes = models.TextField(
        verbose_name=_("Decision Notes"),
        blank=True,
        help_text=_("Reviewer's reasoning for the decision."),
    )
    reviewed_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Reviewed By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Admin/Super Admin who decided this appeal."),
    )
    reviewed_at = models.DateTimeField(
        verbose_name=_("Reviewed At"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("Course Appeal")
        verbose_name_plural = _("Course Appeals")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(fields=["status"], name="course_appeal_status_idx"),
        ]

    def __str__(self):
        """Summarize the appeal for admin/debugging readability."""

        return f"CourseAppeal({self.course_id}, {self.status})"
