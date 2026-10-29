"""Awarding, revoking and reading creator badges.

Every write path funnels through `_grant`, which inserts with
ignore_conflicts and then reads back which rows actually landed, so a
creator is never double-awarded and never told about an award that a
concurrent writer beat them to.
"""

from collections import defaultdict

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from rest_framework import exceptions

from api.achievements.enums import AwardSource
from api.achievements.exceptions import BadgeConflict
from api.achievements.models import Badge, CreatorBadge
from api.achievements.services import criterion_service
from api.authentication.services import activity_service
from api.notification.models import Notification
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.models import User


def schedule_evaluation(*, creator_id, criterion: str) -> None:
    """Queue a badge check for `creator_id` once the caller's transaction commits.

    Called from the course lifecycle (create, content review, approval,
    publish). After commit so a rolled-back transition never awards anything,
    and through Celery so a badge failure never fails the course transition.
    """

    from api.achievements.tasks import evaluate_creator_badges_task

    transaction.on_commit(
        lambda: evaluate_creator_badges_task.delay(str(creator_id), str(criterion))
    )


def evaluate_creator(*, creator_id, criterion: str) -> list[CreatorBadge]:
    """Award every auto-awarded badge on `criterion` the creator now qualifies for.

    Query count is fixed however many badges qualify: the creator, their
    count, the qualifying badges, then (only if any) one bulk insert and its
    read-back, one notification and one bulk audit write.
    """

    creator = User.objects.filter(
        id=creator_id, role__in=criterion_service.EARNING_ROLES
    ).first()
    if creator is None:
        return []

    total = criterion_service.count_for_creator(
        creator_id=creator.id, criterion=criterion
    )
    already_held = CreatorBadge.objects.filter(
        badge=OuterRef("pk"), creator_id=creator.id, is_deleted=False
    )
    badges = list(
        Badge.objects.filter(
            is_deleted=False,
            auto_award=True,
            criterion=criterion,
            required_count__lte=total,
        ).exclude(Exists(already_held))
    )
    if not badges:
        return []

    with transaction.atomic():
        awards = _grant(
            pairs=[(badge, creator) for badge in badges], source=AwardSource.AUTOMATIC
        )
        _record_awards(awards=awards)
    return awards


def backfill(*, badge: Badge) -> list[CreatorBadge]:
    """Award `badge` to every creator who already qualifies for it.

    Runs when a badge becomes auto-awarded or easier to earn, so creators who
    crossed the line before the badge existed are not left out. A fixed
    number of queries regardless of how many creators qualify.
    """

    if badge.is_deleted or not badge.auto_award:
        return []

    creator_ids = criterion_service.creators_reaching(
        criterion=badge.criterion, required_count=badge.required_count
    )
    if not creator_ids:
        return []

    creators = list(User.objects.filter(id__in=creator_ids))
    awards = _grant(
        pairs=[(badge, creator) for creator in creators], source=AwardSource.AUTOMATIC
    )
    _record_awards(awards=awards)
    return awards


def carry_over(*, badge: Badge, creators: list, actor: User) -> list[CreatorBadge]:
    """Give `creators` the badge they are being moved down to on a deletion.

    Audited like any award but not announced on its own - the deletion sends
    one notification that covers both the loss and the move.
    """

    awards = _grant(
        pairs=[(badge, creator) for creator in creators],
        source=AwardSource.CARRIED_OVER,
        actor=actor,
    )
    _record_awards(awards=awards, actor=actor, notify=False)
    return awards


def award_manually(*, badge: Badge, creator_id, actor: User) -> CreatorBadge:
    """Award `badge` to one creator by hand, whatever their count.

    This is how a badge with auto_award off is ever held. A user who is not in
    an earning role is reported as not found, the same as an unknown id.
    """

    creator = User.objects.filter(
        id=creator_id, role__in=criterion_service.EARNING_ROLES
    ).first()
    if creator is None:
        raise exceptions.NotFound("Creator not found.")

    with transaction.atomic():
        awards = _grant(
            pairs=[(badge, creator)], source=AwardSource.MANUAL, actor=actor
        )
        if not awards:
            raise BadgeConflict("This creator already holds this badge.")
        _record_awards(awards=awards, actor=actor)
    return awards[0]


def revoke_award(*, badge: Badge, creator_id, actor: User) -> None:
    """Take `badge` away from one creator.

    If the badge is auto-awarded and the creator still qualifies, their next
    qualifying course event awards it again - revoke is for mistakes, not a
    permanent ban.
    """

    award = (
        CreatorBadge.objects.select_related("creator")
        .filter(badge=badge, creator_id=creator_id, is_deleted=False)
        .first()
    )
    if award is None:
        raise exceptions.NotFound("This creator does not hold this badge.")

    with transaction.atomic():
        award.delete()
        activity_service.log_activity(
            user=award.creator,
            actor_user=actor,
            category=UserActivityCategoryEnums.ACHIEVEMENT,
            action=UserActivityActionEnums.BADGE_REVOKED,
            summary=f"The '{badge.title}' badge was revoked.",
            details={"badge_id": str(badge.id)},
            target=badge,
        )


def list_holders(*, badge: Badge):
    """Live awards of `badge`, newest first, with the creator joined in."""

    return (
        CreatorBadge.objects.filter(badge=badge, is_deleted=False)
        .select_related("creator", "awarded_by")
        .order_by("-awarded_at", "id")
    )


def held_badges(*, user) -> list[CreatorBadge]:
    """The live badges `user` holds, for their profile. One query."""

    return list(
        CreatorBadge.objects.filter(
            creator=user, is_deleted=False, badge__is_deleted=False
        )
        .select_related("badge")
        .order_by("-awarded_at")
    )


def creator_achievements(*, creator: User) -> list[dict]:
    """Every live badge with the creator's progress towards it.

    Three queries however many badges exist: all four counts at once, the
    creator's awards, and the badges.
    """

    counts = criterion_service.counts_for_creator(creator_id=creator.id)
    awards = {award.badge_id: award for award in held_badges(user=creator)}
    badges = Badge.objects.filter(is_deleted=False).order_by(
        "criterion", "required_count"
    )
    return [
        {
            "badge": badge,
            "earned": badge.id in awards,
            "awarded_at": awards[badge.id].awarded_at if badge.id in awards else None,
            "current_count": counts[str(badge.criterion)],
        }
        for badge in badges
    ]


def _grant(
    *, pairs: list, source: str, actor: User | None = None
) -> list[CreatorBadge]:
    """Insert awards, skipping any the creator already holds.

    Returns only the rows that were actually inserted: the ids are generated
    here, so one read-back tells inserted apart from skipped conflicts.
    """

    if not pairs:
        return []
    now = timezone.now()
    candidates = [
        CreatorBadge(
            badge=badge,
            creator=creator,
            source=source,
            awarded_by=actor,
            awarded_at=now,
        )
        for badge, creator in pairs
    ]
    CreatorBadge.objects.bulk_create(candidates, ignore_conflicts=True)
    inserted_ids = set(
        CreatorBadge.objects.filter(id__in=[c.id for c in candidates]).values_list(
            "id", flat=True
        )
    )
    return [candidate for candidate in candidates if candidate.id in inserted_ids]


def _record_awards(
    *, awards: list, actor: User | None = None, notify: bool = True
) -> None:
    """Audit every award in one write and tell each creator what they earned.

    Creators who earned the same set of badges share one notification call,
    so a backfill across many creators is still one insert.
    """

    if not awards:
        return

    activity_service.bulk_log_activity(
        entries=[
            {
                "user": award.creator,
                "actor_user": actor,
                "category": UserActivityCategoryEnums.ACHIEVEMENT,
                "action": UserActivityActionEnums.BADGE_AWARDED,
                "summary": f"Earned the '{award.badge.title}' badge.",
                "details": {"badge_id": str(award.badge_id), "source": award.source},
                "target": award.badge,
            }
            for award in awards
        ]
    )

    if not notify:
        return

    titles_by_creator = defaultdict(list)
    creators = {}
    for award in awards:
        titles_by_creator[award.creator.id].append(award.badge.title)
        creators[award.creator.id] = award.creator

    receivers_by_titles = defaultdict(list)
    for creator_id, titles in titles_by_creator.items():
        receivers_by_titles[tuple(sorted(titles))].append(creators[creator_id])

    for titles, receivers in receivers_by_titles.items():
        quoted = ", ".join(f"'{title}'" for title in titles)
        noun = "badge" if len(titles) == 1 else "badges"
        Notification.emit_in_app_notification(
            receivers=receivers,
            title="You earned a badge" if len(titles) == 1 else "You earned new badges",
            content=f"Congratulations! You earned the {quoted} {noun}.",
            metadata={"badge_titles": list(titles)},
        )
