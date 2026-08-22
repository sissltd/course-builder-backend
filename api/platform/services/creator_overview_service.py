from django.db.models import Count
from django.utils import timezone

from api.collaborators.enums import CollaboratorInviteStatus
from api.collaborators.models import CollaboratorInvite
from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.users.permissions import IsCourseCreatorRole, require_role
from api.wallet.services import wallet_service


def get_overview(*, actor) -> dict:
    """Aggregate the counts a creator's home screen leads with.

    Mirrors admin_overview_service.get_overview: counts rather than lists,
    one grouped query per block, every status key always present so tiles
    are stable. Answers 'what am I working on and what needs me?' in one
    call - the course-builder home screen's load request.
    """

    require_role(actor, IsCourseCreatorRole.allowed_roles)

    wallet = wallet_service.get_or_create_wallet(user=actor)
    pending_invites = (
        CollaboratorInvite.objects.filter(
            email__iexact=actor.email,
            status=CollaboratorInviteStatus.PENDING,
            expires_at__gt=timezone.now(),
        ).count()
    )

    return {
        "courses": _counts_by(
            Course.objects.filter(creator=actor), "status", CourseStatus
        ),
        "wallet": {
            "balance": wallet.balance,
            "currency": wallet.currency,
            **wallet_service.get_wallet_totals(wallet=wallet),
        },
        "pending_invites": pending_invites,
    }


def _counts_by(queryset, field: str, choices) -> dict:
    """Count `queryset` grouped by `field`, with every choice present."""

    counted = {
        row[field]: row["count"]
        for row in queryset.values(field).annotate(count=Count("id"))
    }
    return {value: counted.get(value, 0) for value in choices.values}
