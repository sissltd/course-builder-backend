from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.reviews.enums import ReviewActionType
from api.reviews.models import ReviewAction
from api.users.permissions import IsCreatorReviewerRole, require_role


def get_overview(*, actor) -> dict:
    """Aggregate the counts a reviewer's home screen leads with.

    Mirrors admin_overview_service.get_overview: counts rather than lists,
    zero-filled status keys for stable tiles. The queue block tells the
    reviewer what is waiting; the my_decisions block shows their own
    lifetime approve/reject split plus today's volume.
    """

    require_role(actor, IsCreatorReviewerRole.allowed_roles)

    queue_statuses = (CourseStatus.SUBMITTED, CourseStatus.IN_REVIEW)
    counted = {
        row["status"]: row["count"]
        for row in (
            Course.objects.filter(status__in=queue_statuses)
            .values("status")
            .annotate(count=Count("id"))
        )
    }
    queue = {status.value: counted.get(status.value, 0) for status in queue_statuses}

    decisions = {
        row["action"]: row["count"]
        for row in (
            ReviewAction.objects.filter(reviewer=actor)
            .values("action")
            .annotate(count=Count("id"))
        )
    }
    my_decisions = {
        "approved": decisions.get(ReviewActionType.APPROVE.value, 0),
        "today": _count_actions_today(actor),
    }

    return {"queue": queue, "my_decisions": my_decisions}


def _count_actions_today(reviewer) -> int:
    """How many decisions `reviewer` has recorded since local midnight."""

    today = timezone.localdate()
    return (
        ReviewAction.objects.filter(reviewer=reviewer)
        .annotate(day=TruncDate("created_datetime"))
        .filter(day=today)
        .count()
    )
