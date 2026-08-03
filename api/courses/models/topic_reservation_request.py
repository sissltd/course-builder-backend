from django.db import models
from django.utils.translation import gettext_lazy as _

from api.courses.enums import ReservationStatus
from includes.helpers import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class TopicReservationRequest(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A creator's request for a brand-new Topic under a Category, reserved
    against AI production once approved (PRD BR-007).

    Mirrors CategoryRequest's shape: the creator proposes a `name` under a
    `category`, for a Topic that doesn't exist yet (an *existing* Topic is
    reserved automatically the moment a Draft course selects it - see
    course_service.create_draft_course - this flow is only for a name that
    doesn't exist yet). Approving creates the real Topic and reserves it to
    the requester in one step (see
    topic_reservation_service.approve_request); rejecting just closes the
    request out, optionally with `rejection_reason` (e.g. the name already
    matches an existing topic - that judgment is left to the reviewing
    admin/reviewer, not automated). `topic` stays null until approval.
    """

    requested_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Requested By"),
        on_delete=models.CASCADE,
        related_name="topic_reservation_requests",
        help_text=_("Creator who requested this topic."),
    )
    name = models.CharField(
        verbose_name=_("Name"),
        max_length=255,
        help_text=_("Proposed name for the new topic."),
    )
    category = models.ForeignKey(
        "categories.Category",
        verbose_name=_("Category"),
        on_delete=models.CASCADE,
        related_name="topic_reservation_requests",
        help_text=_("Category the proposed topic would belong to."),
    )
    topic = models.ForeignKey(
        "courses.Topic",
        verbose_name=_("Topic"),
        on_delete=models.CASCADE,
        related_name="reservation_requests",
        null=True,
        blank=True,
        help_text=_("The Topic created once this request is approved."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=ReservationStatus.choices,
        default=ReservationStatus.PENDING,
        help_text=_("Whether this request is pending, approved, or rejected."),
    )
    rejection_reason = models.TextField(
        verbose_name=_("Rejection Reason"),
        null=True,
        blank=True,
        help_text=_("Why the reviewer rejected this request, e.g. a duplicate name."),
    )
    reviewed_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Reviewed By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Admin/Reviewer who approved or rejected this request."),
    )
    reviewed_at = models.DateTimeField(
        verbose_name=_("Reviewed At"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("Topic Reservation Request")
        verbose_name_plural = _("Topic Reservation Requests")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(fields=["status"], name="topic_reservation_status_idx"),
        ]

    def __str__(self):
        """Summarize the request for admin/debugging readability."""

        return f"TopicReservationRequest({self.name}, {self.status})"
