"""Creator requests for a Category that does not exist yet.

Mirrors topic_reservation_service: a creator files a Pending request,
admins decide it, and approval is the moment the real Category comes into
existence. Kept separate from Category itself so an unapproved request
can never behave like a usable category.

Approval needs a price, which the requester cannot be trusted to set -
it determines what the platform pays out - so the deciding admin supplies
it. That is the one meaningful difference from the topic flow, where the
price is inherited from the parent category.
"""

from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import exceptions

from api.catalog.enums import CategoryRequestStatus
from api.catalog.models import Category, CategoryRequest
from api.catalog.services import category_service
from api.notification.models import Notification
from api.notification.services.email_service import send_templated_email
from api.users.models import User
from api.users.permissions import IsAdminRole


def submit_request(
    *, user: User, name: str, description: str = ""
) -> CategoryRequest:
    """File a Pending request and notify admins.

    A name colliding with an existing category is not rejected up front -
    whether it is a genuine duplicate is a judgment call for the admin,
    the same stance topic reservations take.
    """

    request = CategoryRequest.objects.create(
        requested_by=user, name=name, description=description
    )

    admins = list(User.objects.filter(role__in=IsAdminRole.allowed_roles))
    if admins:
        Notification.emit_in_app_notification(
            receivers=admins,
            title="New category request",
            content=f"{user.email} requested a new category: '{name}'.",
            metadata={"category_request_id": str(request.id)},
        )
    return request


def approve_request(
    *, request: CategoryRequest, actor: User, creator_price, track_preference=None
) -> CategoryRequest:
    """Approve a Pending request, creating the real Category.

    The requester is emailed on success. Email failure is swallowed: the
    category exists and the request is decided, so raising here would roll
    a completed decision back over a mail-server problem.
    """

    require_admin(actor)
    if request.status != CategoryRequestStatus.PENDING:
        raise exceptions.ValidationError(
            f"Request cannot be approved from status '{request.status}'."
        )

    fields = {
        "name": request.name,
        "slug": slugify(request.name),
        "description": request.description,
        "creator_price": creator_price,
    }
    if track_preference is not None:
        fields["track_preference"] = track_preference

    # Cheap pre-check for the common case, so the caller gets a clear
    # message rather than a constraint violation.
    if Category.objects.filter(
        models.Q(name__iexact=request.name) | models.Q(slug=fields["slug"])
    ).exists():
        raise exceptions.ValidationError(
            f"A category named '{request.name}' already exists."
        )

    try:
        # The pre-check above races; the unique constraint is the real
        # guarantee. Catching IntegrityError requires its own atomic block
        # or the outer transaction is left unusable.
        with transaction.atomic():
            category = category_service.create_category(actor=actor, **fields)
    except IntegrityError as exc:
        raise exceptions.ValidationError(
            "A category with this name or slug already exists."
        ) from exc

    request.resulting_category = category
    request.status = CategoryRequestStatus.APPROVED
    request.reviewed_by = actor
    request.reviewed_at = timezone.now()
    request.save(
        update_fields=[
            "resulting_category",
            "status",
            "reviewed_by",
            "reviewed_at",
            "updated_datetime",
        ]
    )

    _notify_approved(request)
    return request


def reject_request(*, request: CategoryRequest, actor: User) -> CategoryRequest:
    """Reject a Pending request. No Category is created and no email is
    sent - there is no rejection-notice screen in the designs."""

    require_admin(actor)
    if request.status != CategoryRequestStatus.PENDING:
        raise exceptions.ValidationError(
            f"Request cannot be rejected from status '{request.status}'."
        )

    request.status = CategoryRequestStatus.REJECTED
    request.reviewed_by = actor
    request.reviewed_at = timezone.now()
    request.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "updated_datetime"]
    )
    return request


def require_admin(actor: User) -> None:
    """Service-level role gate, alongside the view's permission class."""

    from api.users.permissions import require_role

    require_role(actor, IsAdminRole.allowed_roles)


def _notify_approved(request: CategoryRequest) -> None:
    try:
        send_templated_email(
            receivers=[request.requested_by.email],
            subject=f"Your category '{request.name}' was approved",
            template_name="emails/category_request_approved",
            context={
                "first_name": request.requested_by.first_name or "there",
                "category_name": request.name,
            },
        )
    except Exception:  # noqa: BLE001 - see docstring: never undo a decision
        import logging

        logging.getLogger(__name__).exception(
            "category request %s approved but the notification email failed",
            request.id,
        )
