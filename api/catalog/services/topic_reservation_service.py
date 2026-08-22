from datetime import timedelta

from django.db import IntegrityError
from django.utils import timezone
from rest_framework import exceptions

from api.catalog.enums import ReservationStatus
from api.catalog.models import Category, Topic, TopicReservationRequest
from api.notification.models import Notification
from api.platform.services import platform_settings_service
from api.users.models import User
from api.users.permissions import IsAdminRole, IsCreatorReviewerRole


def submit_request(
    *, user: User, name: str, category: Category
) -> TopicReservationRequest:
    """Create a Pending TopicReservationRequest for a brand-new topic and
    notify Admins/Reviewers.

    Unlike the old select-an-existing-topic flow, this never rejects a
    duplicate name up front - whether "Fundamentals of Programming" already
    exists is a judgment call left to the reviewing admin/reviewer (see
    approve_request/reject_request), not an automated check at submit time.
    """

    request = TopicReservationRequest.objects.create(
        requested_by=user, name=name, category=category
    )

    managers = list(
        User.objects.filter(
            role__in=IsAdminRole.allowed_roles + IsCreatorReviewerRole.allowed_roles
        )
    )
    if managers:
        Notification.emit_in_app_notification(
            receivers=managers,
            title="New topic request",
            content=f"{user.email} requested a new topic: '{name}'.",
            metadata={"topic_reservation_request_id": request.id},
        )

    return request


def approve_request(
    *, request: TopicReservationRequest, actor: User
) -> TopicReservationRequest:
    """Approve a Pending request: create the real Topic under the requested
    category (price inherited from the category), and reserve it to the
    requester for platform_settings.topic_reservation_expiry_days - all in
    one step, since the whole point of asking was to claim the topic."""

    if request.status != ReservationStatus.PENDING:
        raise exceptions.ValidationError(
            f"Request cannot be approved from status '{request.status}'."
        )

    expiry_days = platform_settings_service.get_settings().topic_reservation_expiry_days

    try:
        topic = Topic.objects.create(
            category=request.category,
            name=request.name,
            creator_price=request.category.creator_price,
            reserved_by=request.requested_by,
            reserved_until=timezone.localdate() + timedelta(days=expiry_days),
        )
    except IntegrityError as exc:
        raise exceptions.ValidationError(
            "A topic with this name already exists in this category."
        ) from exc

    request.topic = topic
    request.status = ReservationStatus.APPROVED
    request.reviewed_by = actor
    request.reviewed_at = timezone.now()
    request.save(
        update_fields=[
            "topic",
            "status",
            "reviewed_by",
            "reviewed_at",
            "updated_datetime",
        ]
    )
    return request


def reject_request(
    *, request: TopicReservationRequest, actor: User, reason: str = ""
) -> TopicReservationRequest:
    """Reject a Pending request. No email - Figma has no rejection-notice
    screen. No Topic is created; `reason` is free text from the reviewer,
    e.g. that the name already matches an existing topic."""

    if request.status != ReservationStatus.PENDING:
        raise exceptions.ValidationError(
            f"Request cannot be rejected from status '{request.status}'."
        )

    request.status = ReservationStatus.REJECTED
    request.rejection_reason = reason or None
    request.reviewed_by = actor
    request.reviewed_at = timezone.now()
    request.save(
        update_fields=[
            "status",
            "rejection_reason",
            "reviewed_by",
            "reviewed_at",
            "updated_datetime",
        ]
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
