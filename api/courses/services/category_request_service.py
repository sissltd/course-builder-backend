from decimal import Decimal

from django.db import IntegrityError
from django.utils import timezone
from rest_framework import exceptions

from api.courses.enums import CategoryRequestStatus, TrackPreference
from api.courses.models import Category, CategoryRequest
from api.notification.models import Notification
from api.users.enums import UserRole
from api.users.models import User

CATEGORY_APPROVED_SUBJECT = "Your category request has been approved"


def submit_request(*, user: User, name: str) -> CategoryRequest:
    """Create a Pending CategoryRequest and notify Admins/Reviewers in-app.

    No email is sent here - the requester is only emailed on approval, per
    the "notified via email shortly on approval" confirmation copy.
    """

    category_request = CategoryRequest.objects.create(requested_by=user, name=name)

    managers = list(
        User.objects.filter(role__in=[UserRole.ADMIN, UserRole.CREATOR_REVIEWER])
    )
    if managers:
        Notification.emit_in_app_notification(
            receivers=managers,
            title="New category request",
            content=f"{user.email} requested a new category: '{name}'.",
            metadata={"category_request_id": category_request.id},
        )

    return category_request


def approve_request(
    *,
    category_request: CategoryRequest,
    actor: User,
    creator_price: Decimal,
    track_preference: str = TrackPreference.OPEN,
) -> CategoryRequest:
    """Approve a Pending request: create the real Category and email the requester.

    `actor` is whoever is allowed to manage category requests - an Admin or
    a Creator Reviewer (Reviewers have full Category/Topic CRUD parity with
    Admin, so they approve/reject these too).
    """

    if category_request.status != CategoryRequestStatus.PENDING:
        raise exceptions.ValidationError(
            f"Request cannot be approved from status '{category_request.status}'."
        )

    try:
        category = Category.objects.create(
            name=category_request.name,
            creator_price=creator_price,
            track_preference=track_preference,
            created_by=actor,
            updated_by=actor,
        )
    except IntegrityError as exc:
        raise exceptions.ValidationError(
            "A category with this name already exists."
        ) from exc

    category_request.resulting_category = category
    category_request.status = CategoryRequestStatus.APPROVED
    category_request.reviewed_by = actor
    category_request.reviewed_at = timezone.now()
    category_request.save(
        update_fields=[
            "resulting_category",
            "status",
            "reviewed_by",
            "reviewed_at",
            "updated_datetime",
        ]
    )

    Notification.emit_email_notification(
        receivers=[category_request.requested_by],
        subject=CATEGORY_APPROVED_SUBJECT,
        template_name="emails/category_request_approved",
        context={
            "first_name": category_request.requested_by.first_name,
            "category_name": category.name,
        },
    )

    return category_request


def reject_request(*, category_request: CategoryRequest, actor: User) -> CategoryRequest:
    """Reject a Pending request. No email - Figma has no rejection-notice screen."""

    if category_request.status != CategoryRequestStatus.PENDING:
        raise exceptions.ValidationError(
            f"Request cannot be rejected from status '{category_request.status}'."
        )

    category_request.status = CategoryRequestStatus.REJECTED
    category_request.reviewed_by = actor
    category_request.reviewed_at = timezone.now()
    category_request.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "updated_datetime"]
    )
    return category_request
