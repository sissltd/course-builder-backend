from datetime import timedelta

from django.utils import timezone
from rest_framework import exceptions

from api.courses.enums import ReservationStatus
from api.courses.models import Topic, TopicReservationRequest
from api.notification.models import Notification
from api.platform.services import platform_settings_service
from api.users.models import User
from api.users.permissions import IsAdminRole, IsCreatorReviewerRole


def submit_request(*, user: User, topic: Topic) -> TopicReservationRequest:
    """Create a Pending TopicReservationRequest and notify Admins/Reviewers.

    Rejects if the topic is already reserved, or already has a Pending
    request awaiting review - a creator should not be able to queue
    multiple concurrent requests for the same topic.
    """

    if topic.is_currently_reserved:
        raise exceptions.ValidationError("This topic is already reserved.")
    if TopicReservationRequest.objects.filter(
        topic=topic, status=ReservationStatus.PENDING
    ).exists():
        raise exceptions.ValidationError(
            "A reservation request for this topic is already pending review."
        )

    request = TopicReservationRequest.objects.create(requested_by=user, topic=topic)

    managers = list(
        User.objects.filter(
            role__in=IsAdminRole.allowed_roles + IsCreatorReviewerRole.allowed_roles
        )
    )
    if managers:
        Notification.emit_in_app_notification(
            receivers=managers,
            title="New topic reservation request",
            content=f"{user.email} requested to reserve topic '{topic.name}'.",
            metadata={"topic_reservation_request_id": request.id},
        )

    return request


def approve_request(
    *, request: TopicReservationRequest, actor: User
) -> TopicReservationRequest:
    """Approve a Pending request: reserve the topic for
    platform_settings.topic_reservation_expiry_days."""

    if request.status != ReservationStatus.PENDING:
        raise exceptions.ValidationError(
            f"Request cannot be approved from status '{request.status}'."
        )

    expiry_days = platform_settings_service.get_settings().topic_reservation_expiry_days

    request.topic.reserved_by = request.requested_by
    request.topic.reserved_until = timezone.localdate() + timedelta(days=expiry_days)
    request.topic.save(update_fields=["reserved_by", "reserved_until", "updated_datetime"])

    request.status = ReservationStatus.APPROVED
    request.reviewed_by = actor
    request.reviewed_at = timezone.now()
    request.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "updated_datetime"]
    )
    return request


def reject_request(
    *, request: TopicReservationRequest, actor: User
) -> TopicReservationRequest:
    """Reject a Pending request. No email - Figma has no rejection-notice screen."""

    if request.status != ReservationStatus.PENDING:
        raise exceptions.ValidationError(
            f"Request cannot be rejected from status '{request.status}'."
        )

    request.status = ReservationStatus.REJECTED
    request.reviewed_by = actor
    request.reviewed_at = timezone.now()
    request.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "updated_datetime"]
    )
    return request


def release_reservation(*, topic: Topic, actor: User) -> Topic:
    """Manually clear a topic's active reservation (PRD: "Admin can manually
    release a reservation if a creator becomes inactive"). A no-op if the
    topic isn't currently reserved, rather than an error - releasing an
    already-free topic is a harmless idempotent action."""

    topic.reserved_by = None
    topic.reserved_until = None
    topic.save(update_fields=["reserved_by", "reserved_until", "updated_datetime"])
    return topic
