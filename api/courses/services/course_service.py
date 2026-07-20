from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework import exceptions

from api.courses.enums import CategoryStatus, CourseStatus
from api.courses.models import Category, Course, Topic
from api.courses.services import course_validation_service
from api.notification.models import Notification
from api.users.models import User

DRAFT_EDITABLE_FIELDS = {
    "title",
    "description",
    "preview_video_url",
    "thumbnail_url",
    "category",
    "topic",
    "difficulty_level",
    "learning_objectives",
    "tags",
    "planned_duration_seconds",
}


def _validate_topic_matches_category(*, topic: Topic | None, category: Category) -> None:
    if topic is not None and topic.category_id != category.id:
        raise exceptions.ValidationError(
            "topic does not belong to the selected category."
        )


def create_draft_course(
    *,
    creator: User,
    category: Category,
    title: str,
    description: str,
    preview_video_url: str = "",
    thumbnail_url: str = "",
    topic: Topic | None = None,
    difficulty_level: str = "",
    learning_objectives: list | None = None,
    tags: list | None = None,
    duration_hours: int = 0,
    duration_minutes: int = 0,
    duration_seconds: int = 0,
    terms_accepted: bool,
) -> Course:
    """Create a new Draft course owned by `creator`.

    Raises ValidationError if terms_accepted is False (BR-005), the category
    is not currently accepting submissions, or topic doesn't belong to
    category. Does not snapshot the category/topic price yet - that happens
    at submit time, see submit_course().
    """

    if not terms_accepted:
        raise exceptions.ValidationError(
            "You must accept the category Terms and Conditions to create a course."
        )
    if category.status != CategoryStatus.ACTIVE:
        raise exceptions.ValidationError(
            "This category is not currently accepting new courses."
        )
    _validate_topic_matches_category(topic=topic, category=category)

    return Course.objects.create(
        creator=creator,
        category=category,
        topic=topic,
        title=title,
        description=description,
        preview_video_url=preview_video_url,
        thumbnail_url=thumbnail_url,
        difficulty_level=difficulty_level,
        learning_objectives=learning_objectives or [],
        tags=tags or [],
        planned_duration_seconds=duration_hours * 3600
        + duration_minutes * 60
        + duration_seconds,
        terms_accepted_at=timezone.now(),
        created_by=creator,
        updated_by=creator,
    )


def update_draft_course(*, course: Course, actor: User, data: dict) -> Course:
    """Update editable fields on a Draft course. Raises ValidationError if the
    course is not in Draft status, or a supplied topic doesn't belong to the
    (new or existing) category."""

    if course.status != CourseStatus.DRAFT:
        raise exceptions.ValidationError("Only Draft courses can be edited.")

    if "topic" in data:
        category = data.get("category", course.category)
        _validate_topic_matches_category(topic=data["topic"], category=category)

    for field, value in data.items():
        if field in DRAFT_EDITABLE_FIELDS:
            setattr(course, field, value)

    course.updated_by = actor
    course.save()
    return course


def delete_draft_course(*, course: Course, actor: User) -> None:
    """Delete a Draft course. Raises ValidationError if not in Draft status."""

    if course.status != CourseStatus.DRAFT:
        raise exceptions.ValidationError("Only Draft courses can be deleted.")
    course.delete()


def submit_course(*, course: Course, actor: User) -> Course:
    """Transition a Draft course to Submitted.

    - Only the owning creator may submit (ownership is also enforced by the
      IsCourseOwner object permission, this is a service-level defense in depth).
    - The course must currently be Draft (BR-001: no bypassing review).
    - Runs course_validation_service.validate_structural_standards(); any
      failures abort the transition with an aggregated ValidationError.
    - Snapshots the category's current creator_price onto the course, read
      literally per "pricing applies only to new submissions" - a category
      price change mid-draft is picked up at submission time, not frozen at
      draft creation.
    """

    if course.creator_id != actor.id:
        raise exceptions.ValidationError(
            "Only the course creator can submit this course."
        )
    if course.status != CourseStatus.DRAFT:
        raise exceptions.ValidationError(
            f"Course cannot be submitted from status '{course.status}'."
        )

    failures = course_validation_service.validate_structural_standards(course)
    if failures:
        raise exceptions.ValidationError({"structural_standards": failures})

    with transaction.atomic():
        course.creator_price_snapshot = (
            course.topic.creator_price if course.topic_id else course.category.creator_price
        )
        course.status = CourseStatus.SUBMITTED
        course.submitted_at = timezone.now()
        course.updated_by = actor
        course.save(
            update_fields=[
                "creator_price_snapshot",
                "status",
                "submitted_at",
                "updated_by",
                "updated_datetime",
            ]
        )
        Notification.emit_in_app_notification(
            receivers=[course.creator],
            title="Course submitted",
            content=f"Your course '{course.title}' has been submitted for review.",
            metadata={"course_id": course.id},
        )

    return course


def claim_for_review(*, course: Course, reviewer: User) -> Course:
    """Transition a Submitted course to In Review.

    Idempotent: calling this on a course already In Review is a no-op (so two
    reviewers hitting claim in close succession don't error on each other).
    Raises ValidationError for any other status.
    """

    if course.status == CourseStatus.IN_REVIEW:
        return course
    if course.status != CourseStatus.SUBMITTED:
        raise exceptions.ValidationError(
            f"Course cannot be claimed from status '{course.status}'."
        )

    course.status = CourseStatus.IN_REVIEW
    course.save(update_fields=["status", "updated_datetime"])
    return course


def publish_course(*, course: Course, actor: User) -> Course:
    """Transition an Approved course to Published (Admin-only, enforced by view
    permission). No external LMS push - deferred until LMS integration exists."""

    if course.status != CourseStatus.APPROVED:
        raise exceptions.ValidationError(
            f"Course cannot be published from status '{course.status}'."
        )

    course.status = CourseStatus.PUBLISHED
    course.published_at = timezone.now()
    course.updated_by = actor
    course.save(
        update_fields=["status", "published_at", "updated_by", "updated_datetime"]
    )
    return course


def get_review_queue(*, status_in: list | None = None) -> QuerySet[Course]:
    """Return courses awaiting review, ordered oldest-submitted-first so a
    future SLA dashboard/alert can be built on top of this ordering."""

    statuses = status_in or [CourseStatus.SUBMITTED, CourseStatus.IN_REVIEW]
    return (
        Course.objects.filter(status__in=statuses)
        .select_related("category", "creator")
        .order_by("submitted_at")
    )
