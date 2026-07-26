from django.db import models
from django.utils.translation import gettext_lazy as _

from api.courses.enums import ReservationStatus
from includes.helpers import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class TopicReservationRequest(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A creator's request to reserve a Topic against AI production (PRD BR-007).

    Mirrors CategoryRequest's shape: approving sets Topic.reserved_by/
    reserved_until (see topic_reservation_service.approve_request); rejecting
    just closes the request out. The Figma's Reservation page shows a
    Pending/Approved queue with bulk Approve/Reject, which is why this is a
    request-and-approve flow rather than the PRD text's literal description
    of silent automatic reservation on Draft creation.
    """

    requested_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Requested By"),
        on_delete=models.CASCADE,
        related_name="topic_reservation_requests",
        help_text=_("Creator who requested this reservation."),
    )
    topic = models.ForeignKey(
        "courses.Topic",
        verbose_name=_("Topic"),
        on_delete=models.CASCADE,
        related_name="reservation_requests",
        help_text=_("Topic requested to be reserved."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=ReservationStatus.choices,
        default=ReservationStatus.PENDING,
        help_text=_("Whether this request is pending, approved, or rejected."),
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

        return f"TopicReservationRequest({self.topic_id}, {self.status})"
