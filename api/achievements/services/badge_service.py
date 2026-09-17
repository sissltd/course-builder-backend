"""Creating, editing, configuring and deleting badges.

Badges sharing a criterion form a ladder ordered by required_count; that
ordering is what "the previous badge" means when one is deleted.
"""

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, QuerySet
from django.utils import timezone
from rest_framework import exceptions

from api.achievements.exceptions import BadgeConflict
from api.achievements.models import Badge, CreatorBadge
from api.achievements.services import award_service
from api.authentication.services import activity_service
from api.notification.models import Notification
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.models import User

#: Fields an existing badge may change. `criterion` is fixed at creation:
#: switching what a badge counts would leave its holders having earned it
#: for something else. Create a new badge instead.
UPDATABLE_FIELDS = ("title", "icon", "color", "required_count", "auto_award")


def list_badges() -> QuerySet:
    """Live badges with how many creators hold each, highest requirement first.

    One query: the holder count is an annotation, which is also what the
    Achievement Analytics cards read.
    """

    return (
        Badge.objects.filter(is_deleted=False)
        .annotate(holder_count=Count("awards", filter=Q(awards__is_deleted=False)))
        .order_by("-required_count", "title")
    )


def get_live_badge(*, badge_id) -> Badge:
    """A live badge by id, with its holder count. Deleted or unknown is 404."""

    badge = list_badges().filter(id=badge_id).first()
    if badge is None:
        raise exceptions.NotFound("Badge not found.")
    return badge


def create_badge(
    *,
    actor: User,
    title: str,
    icon: str,
    color: str,
    criterion: str,
    required_count: int,
    auto_award: bool,
) -> Badge:
    """Create a badge; if it auto-awards, award it to everyone already qualifying."""

    _assert_no_conflict(title=title, criterion=criterion, required_count=required_count)
    with transaction.atomic():
        try:
            with transaction.atomic():
                badge = Badge.objects.create(
                    title=title,
                    icon=icon,
                    color=color,
                    criterion=criterion,
                    required_count=required_count,
                    auto_award=auto_award,
                    created_by=actor,
                    updated_by=actor,
                )
        except IntegrityError as exc:
            # A concurrent create won the race past _assert_no_conflict.
            raise BadgeConflict() from exc
        awarded = award_service.backfill(badge=badge)
        activity_service.log_activity(
            user=actor,
            category=UserActivityCategoryEnums.CONFIGURATION,
            action=UserActivityActionEnums.BADGE_CREATED,
            summary=f"Created the '{badge.title}' badge.",
            details={"badge_id": str(badge.id), "creators_awarded": len(awarded)},
            target=badge,
        )
    return get_live_badge(badge_id=badge.id)


def update_badge(*, badge: Badge, actor: User, data: dict) -> Badge:
    """Apply an Edit or Configure change; every field is optional.

    Raising `required_count` never takes a badge away from creators who
    already earned it. Lowering it, or switching auto_award on, awards the
    badge to anyone who now qualifies.
    """

    changes = {
        field: data[field]
        for field in UPDATABLE_FIELDS
        if field in data and data[field] != getattr(badge, field)
    }
    if not changes:
        return get_live_badge(badge_id=badge.id)

    _assert_no_conflict(
        title=changes.get("title"),
        criterion=badge.criterion,
        required_count=changes.get("required_count"),
        exclude_id=badge.id,
    )
    became_easier = (
        changes.get("required_count", badge.required_count) < badge.required_count
    )
    switched_on = changes.get("auto_award") is True

    with transaction.atomic():
        for field, value in changes.items():
            setattr(badge, field, value)
        badge.updated_by = actor
        try:
            with transaction.atomic():
                badge.save(update_fields=[*changes, "updated_by", "updated_datetime"])
        except IntegrityError as exc:
            raise BadgeConflict() from exc
        awarded = (
            award_service.backfill(badge=badge) if became_easier or switched_on else []
        )
        activity_service.log_activity(
            user=actor,
            category=UserActivityCategoryEnums.CONFIGURATION,
            action=UserActivityActionEnums.BADGE_UPDATED,
            summary=f"Updated the '{badge.title}' badge.",
            details={
                "badge_id": str(badge.id),
                "changed_fields": sorted(changes),
                "creators_awarded": len(awarded),
            },
            target=badge,
        )
    return get_live_badge(badge_id=badge.id)


def previous_badge(*, badge: Badge) -> Badge | None:
    """The live badge one rung below `badge` on its criterion's ladder."""

    return (
        Badge.objects.filter(
            is_deleted=False,
            criterion=badge.criterion,
            required_count__lt=badge.required_count,
        )
        .order_by("-required_count")
        .first()
    )


def get_deletion_impact(*, badge: Badge) -> dict:
    """What deleting `badge` would do, for the delete confirmation dialog."""

    return {
        "badge_id": badge.id,
        "holder_count": badge.holder_count,
        "previous_badge": previous_badge(badge=badge),
    }


def delete_badge(*, badge: Badge, actor: User, move_to_previous: bool) -> dict:
    """Soft-delete `badge`; its holders lose it, optionally moving down a rung.

    `move_to_previous` with no lower badge on the ladder is a 400 rather than
    a silent no-op, so the caller never believes creators were moved when
    they were not. Holders who already had the previous badge keep their
    original award rather than gaining a duplicate.
    """

    target = previous_badge(badge=badge) if move_to_previous else None
    if move_to_previous and target is None:
        raise exceptions.ValidationError(
            {
                "move_to_previous": (
                    "There is no lower badge on this badge's ladder to move "
                    "creators to."
                )
            }
        )

    with transaction.atomic():
        live_awards = CreatorBadge.objects.filter(badge=badge, is_deleted=False)
        holders = list(User.objects.filter(id__in=live_awards.values("creator_id")))
        moved = (
            award_service.carry_over(badge=target, creators=holders, actor=actor)
            if target
            else []
        )
        now = timezone.now()
        removed = live_awards.update(
            is_deleted=True, deleted_datetime=now, updated_datetime=now
        )
        badge.is_deleted = True
        badge.deleted_datetime = now
        badge.updated_by = actor
        badge.save(
            update_fields=[
                "is_deleted",
                "deleted_datetime",
                "updated_by",
                "updated_datetime",
            ]
        )
        activity_service.log_activity(
            user=actor,
            category=UserActivityCategoryEnums.CONFIGURATION,
            action=UserActivityActionEnums.BADGE_DELETED,
            summary=f"Deleted the '{badge.title}' badge.",
            details={
                "badge_id": str(badge.id),
                "holders_removed": removed,
                "moved_to_badge_id": str(target.id) if target else None,
            },
            target=badge,
        )
        if holders:
            activity_service.bulk_log_activity(
                entries=[
                    {
                        "user": holder,
                        "actor_user": actor,
                        "category": UserActivityCategoryEnums.ACHIEVEMENT,
                        "action": UserActivityActionEnums.BADGE_REVOKED,
                        "summary": f"The '{badge.title}' badge was retired.",
                        "details": {
                            "badge_id": str(badge.id),
                            "reason": "badge_deleted",
                        },
                        "target": badge,
                    }
                    for holder in holders
                ]
            )
            _notify_holders_of_deletion(badge=badge, holders=holders, target=target)

    return {
        "badge_id": badge.id,
        "holders_removed": removed,
        "moved_to_badge": target,
        "holders_moved": len(moved),
    }


def _assert_no_conflict(
    *, title: str | None, criterion: str, required_count: int | None, exclude_id=None
) -> None:
    """409 when a live badge already uses this title or ladder rung."""

    live = Badge.objects.filter(is_deleted=False)
    if exclude_id is not None:
        live = live.exclude(id=exclude_id)
    if title is not None and live.filter(title__iexact=title).exists():
        raise BadgeConflict(f"A badge named '{title}' already exists.")
    if (
        required_count is not None
        and live.filter(criterion=criterion, required_count=required_count).exists()
    ):
        raise BadgeConflict(
            "Another badge already requires this many courses for the same criterion."
        )


def _notify_holders_of_deletion(
    *, badge: Badge, holders: list, target: Badge | None
) -> None:
    content = (
        f"The '{badge.title}' badge has been retired and removed from your profile."
    )
    if target is not None:
        content += f" You now hold the '{target.title}' badge."
    Notification.emit_in_app_notification(
        receivers=holders,
        title="A badge was retired",
        content=content,
        metadata={
            "badge_id": badge.id,
            "moved_to_badge_id": target.id if target else None,
        },
    )
