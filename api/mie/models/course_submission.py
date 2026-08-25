from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from api.mie.enums import SubmissionStatus
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class CourseSubmission(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A course idea submitted by an external developer (Endpoint 1).

    The raw request body is kept verbatim in `payload`; `title` is the
    extracted dedup/indexing key. Status transitions are not terminal -
    a superadmin can reverse APPROVED <-> REJECTED at any time, and every
    transition re-fires a webhook. The public reference shown to the
    developer is derived from (id, current status) so it always reflects
    reality: ...-P pending, -D dup-in-queue, -E dup-existing, -X
    previously-rejected, -A approved, -R rejected; an accepted idea's
    resulting course carries -P for production.
    """

    developer = models.ForeignKey(
        "mie.DeveloperAccount",
        verbose_name=_("Developer"),
        on_delete=models.CASCADE,
        related_name="submissions",
        help_text=_("External account that submitted this idea."),
    )
    payload = models.JSONField(
        verbose_name=("Endpoint 1 Payload"),
        help_text=_("The submission body exactly as received - never rewritten."),
    )
    title = models.CharField(
        verbose_name=_("Idea Title"),
        max_length=255,
        help_text=_(
            "Title extracted from the payload; the key used by all three "
            "dedup checks."
        ),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=25,
        choices=SubmissionStatus.choices,
        default=SubmissionStatus.PENDING_REVIEW,
        help_text=_("Current pipeline state; visible in every queue surface."),
    )
    rejection_reason = models.ForeignKey(
        "mie.SubmissionRejectionReason",
        verbose_name=_("Rejection Reason"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
        help_text=_("Taxonomy reason attached on rejection or dedup short-circuit."),
    )
    rejection_note = models.TextField(
        verbose_name=_("Rejection Note"),
        blank=True,
        help_text=_("Free-text detail accompanying the rejection."),
    )
    demand_score = models.PositiveSmallIntegerField(
        verbose_name=_("Demand Score"),
        null=True,
        blank=True,
        help_text=_(
            "Admin-entered market-demand signal used to prioritise the "
            "Recommendations queue."
        ),
    )
    estimated_monthly_earnings = models.DecimalField(
        verbose_name=_("Estimated Monthly Earnings"),
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Admin-entered market-research estimate, in platform currency."),
    )
    queued_at = models.DateTimeField(
        verbose_name=_("Queued At"),
        null=True,
        blank=True,
        help_text=_("When the idea most recently entered the review queue."),
    )
    decided_at = models.DateTimeField(
        verbose_name=_("Decided At"),
        null=True,
        blank=True,
        help_text=_("When the latest approve/reject decision was taken."),
    )
    decided_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Decided By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mie_submissions_decided",
        help_text=_("Superadmin responsible for the latest decision."),
    )
    payout_bypass = models.BooleanField(
        verbose_name=_("Payout Bypass"),
        default=False,
        help_text=_(
            "Per-submission no-payout marker applied by a superadmin when "
            "the creator will not be paid for this idea."
        ),
    )
    resulting_course = models.OneToOneField(
        "courses.Course",
        verbose_name=_("Resulting Course"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mie_submission",
        help_text=_(
            "Course created when this idea was accepted. On reversal the "
            "course is flagged/unpublished, never deleted, so re-approval "
            "can relink instead of duplicating."
        ),
    )

    class Meta:
        verbose_name = _("Course Submission")
        verbose_name_plural = _("Course Submissions")
        ordering = ["-created_datetime"]
        constraints = [
            models.UniqueConstraint(
                Lower("title"),
                condition=models.Q(status=SubmissionStatus.PENDING_REVIEW),
                name="unique_pending_title_in_queue",
            ),
            models.CheckConstraint(
                check=~models.Q(status__in=("APPROVED", "REJECTED"))
                | models.Q(decided_at__isnull=False),
                name="mie_decided_submission_has_decision",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "-created_datetime"], name="mie_sub_status_idx"),
            models.Index(fields=["developer", "status"], name="mie_sub_dev_status_idx"),
        ]

    @property
    def public_reference(self) -> str:
        """Developer-facing id whose last segment encodes current status."""

        from api.mie.services.reference import REFERENCE_SUFFIXES

        short_id = str(self.id).replace("-", "")[:8]
        return f"SCB-{short_id}-{REFERENCE_SUFFIXES[self.status]}"

    def __str__(self):
        return f"{self.title} ({self.status})"
