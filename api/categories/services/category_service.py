from django.db import transaction
from django.db.models import Count, ProtectedError
from django.utils import timezone
from rest_framework import exceptions

from api.categories.enums import CategoryDeletionStrategy
from api.categories.exceptions import CategoryDeletionNeedsStrategy
from api.categories.models import Category
from api.users.models import User


def create_category(*, actor: User, **fields) -> Category:
    """Create a category, stamping the acting staff member as its author."""

    return Category.objects.create(created_by=actor, updated_by=actor, **fields)


def update_category(*, category: Category, actor: User, data: dict) -> Category:
    """Apply `data` to `category` and record who changed it.

    Editing `creator_price` is allowed and takes effect only for courses
    submitted afterwards - Course.creator_price_snapshot freezes the rate at
    submission time, so no existing payout is altered retroactively.
    """

    editable_fields = {
        "name",
        "description",
        "creator_price",
        "track_preference",
        "status",
    }
    for field, value in data.items():
        if field in editable_fields:
            setattr(category, field, value)

    category.updated_by = actor
    category.save()
    return category


def get_deletion_impact(*, category: Category) -> dict:
    """Summarize what deleting `category` would take with it.

    Powers the warning in the delete confirmation dialog, so the admin decides
    with the numbers in front of them rather than discovering the damage
    afterwards. Import of Course is local: production code in `categories` must
    not depend on `courses` at module scope, since the dependency runs the other
    way.
    """

    from api.courses.models import Course

    courses = Course.objects.filter(category=category)
    status_counts = {
        row["status"]: row["total"]
        for row in courses.values("status").annotate(total=Count("id"))
    }
    course_count = sum(status_counts.values())

    # Counted separately because these are SET_NULL, not PROTECT: they never
    # block the delete and are never deleted by it - they just quietly lose
    # their stated expertise, which is worth warning about.
    profile_count = _creator_profile_count(category)

    return {
        "category_id": category.id,
        "category_name": category.name,
        "course_count": course_count,
        "courses_by_status": status_counts,
        "affected_creator_profile_count": profile_count,
        "requires_strategy": course_count > 0,
    }


def delete_category(
    *,
    category: Category,
    actor: User,
    strategy: str | None = None,
    replacement_category: Category | None = None,
) -> dict:
    """Delete `category`, handling the courses that belong to it.

    With no courses, `strategy` is unnecessary and the category is simply
    removed. With courses, a strategy is required - omitting it raises 409
    rather than guessing, because the two options have very different
    consequences:

    * REASSIGN moves every course to `replacement_category` and keeps them.
      Already-submitted courses keep their frozen `creator_price_snapshot`, so
      no payout changes; drafts will pick up the new category's price when they
      are eventually submitted.
    * DELETE_COURSES destroys them, and the cascade reaches further than the
      courses themselves - modules, lessons, assessments, and review history go
      with them. It is not limited to drafts, so published work can be removed
      this way.

    Runs in a single transaction: courses are never left reassigned or deleted
    while the category itself survives.

    Returns the impact summary describing what actually happened, so the caller
    can report it back to the admin.
    """

    from api.courses.models import Course

    impact = get_deletion_impact(category=category)

    if not impact["requires_strategy"]:
        _delete_or_explain(category)
        return {
            **impact,
            "strategy_applied": None,
            "courses_deleted": 0,
            "courses_reassigned": 0,
        }

    if strategy is None:
        raise CategoryDeletionNeedsStrategy(
            f"'{category.name}' still has {impact['course_count']} course(s). "
            "Choose whether to move them to another category or delete them."
        )

    courses = Course.objects.filter(category=category)
    reassigned = deleted = 0

    with transaction.atomic():
        if strategy == CategoryDeletionStrategy.REASSIGN:
            _assert_valid_replacement(
                category=category, replacement_category=replacement_category
            )
            # Bulk update rather than per-instance saves: this can touch many
            # rows and none of the model's save() behaviour is needed. auto_now
            # does not fire on .update(), so updated_datetime is set by hand to
            # keep the audit trail honest.
            reassigned = courses.update(
                category=replacement_category,
                updated_by=actor,
                updated_datetime=timezone.now(),
            )
        elif strategy == CategoryDeletionStrategy.DELETE_COURSES:
            deleted = impact["course_count"]
            courses.delete()
        else:
            raise exceptions.ValidationError(
                {"strategy": f"'{strategy}' is not a valid deletion strategy."}
            )

        _delete_or_explain(category)

    return {
        **impact,
        "strategy_applied": strategy,
        "courses_deleted": deleted,
        "courses_reassigned": reassigned,
    }


def _assert_valid_replacement(
    *, category: Category, replacement_category: Category | None
) -> None:
    """Validate the target of a REASSIGN before anything is moved."""

    if replacement_category is None:
        raise exceptions.ValidationError(
            {
                "replacement_category": (
                    "A replacement category is required when reassigning courses."
                )
            }
        )
    if replacement_category.id == category.id:
        raise exceptions.ValidationError(
            {
                "replacement_category": (
                    "Courses cannot be reassigned to the category being deleted."
                )
            }
        )


def _creator_profile_count(category: Category) -> int:
    from api.onboarding.models import CreatorProfile

    return CreatorProfile.objects.filter(primary_expertise_category=category).count()


def _delete_or_explain(category: Category) -> None:
    """Delete `category`, converting a PROTECT violation into a 400.

    Course.category is on_delete=PROTECT. The strategies above clear that
    dependency first, so reaching the ProtectedError here means something else
    gained a protected reference to categories - a new model, or a course
    created concurrently between the impact count and the delete. Either way a
    500 would be the wrong answer.
    """

    try:
        category.delete()
    except ProtectedError as exc:
        raise exceptions.ValidationError(
            "This category cannot be deleted because something still "
            "references it. Refresh and try again, or set its status to "
            "'INACTIVE' to stop new submissions."
        ) from exc
