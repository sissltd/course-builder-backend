"""The one definition of what each BadgeCriterion counts.

Everything that needs a creator's count - live awarding, backfill, and the
creator's progress screen - builds it from COUNT_EXPRESSIONS, so the three
can never disagree about what "approved" or "reviewed" means.

All counts are over Course rows the creator owns, counted distinctly because
the REVIEWED expression joins review actions and one course can carry more
than one.
"""

from django.db.models import Count, Q

from api.achievements.enums import BadgeCriterion
from api.courses.models import Course
from api.reviews.enums import ReviewActionType, ReviewStage
from api.users.workflow import EARNING_ROLES

COUNT_EXPRESSIONS = {
    BadgeCriterion.COURSES_CREATED: Count("id", distinct=True),
    # Passing the last content seat is what ends content review
    # (review_service.approve_content), whatever QA later decides.
    BadgeCriterion.COURSES_REVIEWED: Count(
        "id",
        filter=Q(
            review_actions__stage=ReviewStage.VERIFICATION,
            review_actions__action=ReviewActionType.APPROVE,
        ),
        distinct=True,
    ),
    # approved_at survives publishing, so a published course still counts.
    BadgeCriterion.COURSES_APPROVED: Count(
        "id", filter=Q(approved_at__isnull=False), distinct=True
    ),
    BadgeCriterion.COURSES_PUBLISHED: Count(
        "id", filter=Q(published_at__isnull=False), distinct=True
    ),
}

# Only authoring roles (users.workflow.EARNING_ROLES) earn badges; a reviewer
# who happens to own a course row (e.g. via a role change) does not.


def count_for_creator(*, creator_id, criterion: str) -> int:
    """One creator's count for one criterion. One query."""

    return Course.objects.filter(creator_id=creator_id).aggregate(
        total=COUNT_EXPRESSIONS[criterion]
    )["total"]


def counts_for_creator(*, creator_id) -> dict[str, int]:
    """One creator's count for every criterion, in a single query."""

    return Course.objects.filter(creator_id=creator_id).aggregate(
        **{
            str(criterion): expression
            for criterion, expression in COUNT_EXPRESSIONS.items()
        }
    )


def creators_reaching(*, criterion: str, required_count: int) -> list:
    """Ids of every earning-role creator whose count reaches `required_count`.

    One aggregate query regardless of how many creators exist - used to
    backfill a badge that has just become auto-awarded or easier to earn.
    """

    return list(
        Course.objects.filter(creator__role__in=EARNING_ROLES)
        .values("creator_id")
        .annotate(total=COUNT_EXPRESSIONS[criterion])
        .filter(total__gte=required_count)
        .values_list("creator_id", flat=True)
    )
