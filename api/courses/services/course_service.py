from datetime import timedelta

from django.db import transaction
from django.db.models import (
    Case,
    DurationField,
    ExpressionWrapper,
    F,
    IntegerField,
    QuerySet,
    Value,
    When,
)
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.services import activity_service
from api.catalog.enums import CategoryStatus, TrackPreference
from api.catalog.models import Category, Topic
from api.courses.enums import (
    CourseSourceType,
    CourseStatus,
    DistributionChannel,
    DistributionStatus,
)
from api.courses.models import (
    Course,
    CourseDistribution,
    CourseVersion,
    PublishedCourseSnapshot,
)
from api.reviews.enums import ReviewStage
from api.reviews.models import ReviewAssignment
from api.reviews.services import (
    quality_check_service,
    quality_review_service,
)
from api.notification.models import Notification
from api.notification.services import sla_threshold_service
from api.platform.services import platform_settings_service
from api.users.enums import (
    QUEUE_SORT_WINDOW_DAYS,
    QueueTrackFilter,
    UserActivityActionEnums,
    UserActivityCategoryEnums,
)
from api.users.models import User
from api.users.permissions import (
    IsAdminRole,
    IsCourseCreatorRole,
    IsCreatorReviewerRole,
    require_role,
)
from api.users.services import reviewer_availability_service

#: Maps a reviewer's QueueTrackFilter preference onto the Category-level
#: TrackPreference it should filter the queue by. ALL means "no filter".
QUEUE_TRACK_FILTER_TO_CATEGORY_TRACK_PREFERENCE = {
    QueueTrackFilter.CREATOR_TRACK: TrackPreference.CREATOR_PREFERRED,
    QueueTrackFilter.AI_TRACK: TrackPreference.AI_PREFERRED,
}

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
    "version",
}


def _validate_topic_matches_category(
    *, topic: Topic | None, category: Category
) -> None:
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
    version: CourseVersion | None = None,
    duration_hours: int = 0,
    duration_minutes: int = 0,
    duration_seconds: int = 0,
    terms_accepted: bool,
    source_type: str = CourseSourceType.CREATOR_UPLOADED,
) -> Course:
    """Create a new Draft course owned by `creator`.

    Raises ValidationError if terms_accepted is False (BR-005), the category
    is not currently accepting submissions, topic doesn't belong to category,
    or topic is currently reserved by someone else. Does not snapshot the
    category/topic price yet - that happens at submit time, see
    submit_course().

    Selecting an available topic reserves it for `creator` immediately (PRD
    5.2's "Select category/topic -> Start Draft -> Topic automatically
    reserved" flow), using the same expiry window as an approved
    TopicReservationRequest. This is separate from - and does not require -
    the request/approve flow in topic_reservation_service, which exists for
    requesting a brand-new topic that doesn't exist yet.
    """

    require_role(creator, IsCourseCreatorRole.allowed_roles)
    if not terms_accepted:
        raise exceptions.ValidationError(
            "You must accept the category Terms and Conditions to create a course."
        )
    if category.status != CategoryStatus.ACTIVE:
        raise exceptions.ValidationError(
            "This category is not currently accepting new courses."
        )
    _validate_topic_matches_category(topic=topic, category=category)
    if (
        topic is not None
        and topic.is_currently_reserved
        and topic.reserved_by_id != creator.id
    ):
        raise exceptions.ValidationError("This topic is currently reserved.")

    with transaction.atomic():
        course = Course.objects.create(
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
            version=version,
            planned_duration_seconds=duration_hours * 3600
            + duration_minutes * 60
            + duration_seconds,
            terms_accepted_at=timezone.now(),
            source_type=source_type,
            created_by=creator,
            updated_by=creator,
        )

        if topic is not None and not topic.is_currently_reserved:
            expiry_days = (
                platform_settings_service.get_settings().topic_reservation_expiry_days
            )
            topic.reserved_by = creator
            topic.reserved_until = timezone.localdate() + timedelta(days=expiry_days)
            topic.save(
                update_fields=["reserved_by", "reserved_until", "updated_datetime"]
            )

    return course


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
    - Runs quality_check_service.validate_structural_standards(); any
      failures abort the transition with an aggregated ValidationError.
    - Creator-uploaded courses snapshot the current topic/category price.
      AI-generated courses remain unpaid and keep this field null.
    """

    require_role(actor, IsCourseCreatorRole.allowed_roles + IsAdminRole.allowed_roles)
    if course.creator_id != actor.id:
        raise exceptions.ValidationError(
            "Only the course creator can submit this course."
        )
    if course.status != CourseStatus.DRAFT:
        raise exceptions.ValidationError(
            f"Course cannot be submitted from status '{course.status}'."
        )

    failures = quality_check_service.validate_structural_standards(course)
    if failures:
        raise exceptions.ValidationError({"structural_standards": failures})

    with transaction.atomic():
        # A topic-specific price still overrides the category, unchanged.
        # Otherwise the payout follows the course's own difficulty, which
        # is what the category's three price levels exist to express.
        course.creator_price_snapshot = (
            None
            if course.source_type == CourseSourceType.AI_GENERATED
            else (
                course.topic.creator_price
                if course.topic_id
                else course.category.price_for(course.difficulty_level)
            )
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
        # Persist a reviewer-visible baseline score when the course enters the
        # queue. More specialised scanners can append their own runs later.
        quality_review_service.run_baseline_checks(course=course)
        Notification.emit_in_app_notification(
            receivers=[course.creator],
            title="Course submitted",
            content=f"Your course '{course.title}' has been submitted for review.",
            metadata={"course_id": course.id},
        )
        activity_service.log_activity(
            user=course.creator,
            category=UserActivityCategoryEnums.SUBMISSION,
            action=UserActivityActionEnums.COURSE_SUBMITTED,
            summary=f"You submitted '{course.title}' for review.",
            target=course,
        )

    return course


def claim_for_review(*, course: Course, reviewer: User) -> Course:
    """Transition a Submitted course to In Review.

    Idempotent for the assigned reviewer. A row lock makes simultaneous claims
    exclusive; a different reviewer receives a validation error.
    Raises ValidationError for any other status. A reviewer marked
    Unavailable cannot make a *new* claim (checked after the idempotent
    short-circuit, so re-calling claim on a course they already hold still
    works even if they've since gone Unavailable).
    """

    require_role(
        reviewer, IsCreatorReviewerRole.allowed_roles + IsAdminRole.allowed_roles
    )
    with transaction.atomic():
        course = Course.objects.select_for_update().get(pk=course.pk)
        assignment = ReviewAssignment.objects.filter(
            course=course, stage=ReviewStage.CONTENT
        ).first()
        if course.status == CourseStatus.IN_REVIEW:
            if assignment and assignment.reviewer_id == reviewer.id:
                return course
            raise exceptions.ValidationError(
                "This course is already assigned to another reviewer."
            )
        if course.status != CourseStatus.SUBMITTED:
            raise exceptions.ValidationError(
                f"Course cannot be claimed from status '{course.status}'."
            )
        reviewer_availability_service.require_reviewer_available(user=reviewer)

        course.status = CourseStatus.IN_REVIEW
        course.save(update_fields=["status", "updated_datetime"])
        assignment, _ = ReviewAssignment.objects.get_or_create(
            course=course, stage=ReviewStage.CONTENT
        )
        assignment.reviewer = reviewer
        assignment.claimed_at = assignment.claimed_at or timezone.now()
        assignment.save(update_fields=["reviewer", "claimed_at", "updated_datetime"])
    activity_service.log_activity(
        user=reviewer,
        category=UserActivityCategoryEnums.COURSE,
        action=UserActivityActionEnums.COURSE_ASSIGNED,
        summary=f"Course '{course.title}' assigned to you.",
        target=course,
    )
    return course


def _get_publish_version(*, course: Course) -> CourseVersion:
    """Return the CourseVersion label a course should be published under.

    Prefers the course's already-assigned version; otherwise falls back to
    the active version with the lowest label (seeded as "1.0").
    """

    if course.version_id:
        return course.version
    return CourseVersion.objects.filter(is_active=True).order_by("label").first()


def _build_course_snapshot(course: Course) -> dict:
    """Plain-dict snapshot of a course's module/lesson tree at publish time,
    for PublishedCourseSnapshot.snapshot. Built by direct model traversal
    rather than a serializer, to avoid a circular import with
    course_serializer (which already imports this module)."""

    return {
        "title": course.title,
        "description": course.description,
        "difficulty_level": course.difficulty_level,
        "modules": [
            {
                "title": module.title,
                "order": module.order,
                "lessons": [
                    {
                        "title": lesson.title,
                        "order": lesson.order,
                        "script": lesson.script,
                        "video_url": lesson.video_url,
                        "duration_minutes": lesson.duration_minutes,
                    }
                    for lesson in module.lessons.all()
                ],
            }
            for module in course.modules.all()
        ],
    }


def save_distribution_channels(
    *, course: Course, channels: list[dict]
) -> list[CourseDistribution]:
    """Create or update the channel cards from the Review Prices design."""

    if course.status != CourseStatus.APPROVED:
        raise exceptions.ValidationError(
            f"Course prices cannot be saved from status '{course.status}'."
        )
    saved = []
    with transaction.atomic():
        for channel_data in channels:
            values = dict(channel_data)
            channel = values.pop("channel")
            distribution, _ = CourseDistribution.objects.update_or_create(
                course=course,
                channel=channel,
                defaults=values,
            )
            saved.append(distribution)
    return saved


def publish_course(
    *,
    course: Course,
    actor: User,
    distribution_channels: list[dict] | None = None,
) -> Course:
    """Transition an Approved course to Published for a reviewer or admin.

    Records a PublishedCourseSnapshot under the canonical CourseVersion
    label (SCCS PRD Section 15). No external LMS push is attempted; that is
    deferred until each marketplace integration exists.

    There is no re-edit-after-publish workflow yet (publishing is one-way,
    no unpublish action), so this only ever creates a single snapshot per
    course today - see CourseVersion's docstring.
    """

    require_role(
        actor,
        IsCreatorReviewerRole.allowed_roles + IsAdminRole.allowed_roles,
    )
    if course.status != CourseStatus.APPROVED:
        raise exceptions.ValidationError(
            f"Course cannot be published from status '{course.status}'."
        )

    with transaction.atomic():
        course = Course.objects.select_for_update().get(pk=course.pk)
        if course.status != CourseStatus.APPROVED:
            raise exceptions.ValidationError(
                f"Course cannot be published from status '{course.status}'."
            )
        if distribution_channels is not None:
            save_distribution_channels(course=course, channels=distribution_channels)
        version = _get_publish_version(course=course)
        if version is None:
            raise exceptions.ValidationError(
                "No active CourseVersion is available for publishing."
            )
        course.status = CourseStatus.PUBLISHED
        course.version = version
        course.published_at = timezone.now()
        course.updated_by = actor
        course.save(
            update_fields=[
                "status",
                "version",
                "published_at",
                "updated_by",
                "updated_datetime",
            ]
        )
        PublishedCourseSnapshot.objects.create(
            course=course,
            version=version,
            published_at=course.published_at,
            snapshot=_build_course_snapshot(course),
            created_by=actor,
            updated_by=actor,
        )
        now = course.published_at
        CourseDistribution.objects.filter(course=course).update(
            status=DistributionStatus.QUEUED,
            failure_reason="",
            updated_datetime=now,
        )
        CourseDistribution.objects.filter(
            course=course, channel=DistributionChannel.SOLUDESK
        ).update(
            status=DistributionStatus.PUBLISHED,
            published_at=now,
            updated_datetime=now,
        )
        activity_service.log_activity(
            user=actor,
            category=UserActivityCategoryEnums.PUBLISH,
            action=UserActivityActionEnums.COURSE_PUBLISHED,
            summary=f"You published '{course.title}'.",
            target=course,
        )
    return course


def recalculate_duration_estimate(*, course: Course) -> Course:
    """Recompute and persist Course.duration_estimate_minutes from its
    current Lesson tree. Call after any Lesson create/update/delete."""

    course.duration_estimate_minutes = (
        quality_check_service.get_course_duration_minutes(course)
    )
    course.save(update_fields=["duration_estimate_minutes", "updated_datetime"])
    return course


def get_review_queue(
    *,
    status_in: list | None = None,
    sort_order: str | None = None,
    track_filter: str | None = None,
    sla_user: User | None = None,
) -> QuerySet[Course]:
    """Return courses awaiting review.

    Defaults to oldest-submitted-first (unchanged from before Queue
    Behaviour preferences existed). `sort_order` accepts a QueueSortOrder
    value: NEWEST_FIRST reverses the default; LAST_30_DAYS / LAST_7_DAYS /
    LAST_24_HOURS narrow to that window and sort oldest-first; ALL and
    OLDEST_FIRST are the unfiltered default. SLA_URGENCY is still
    honoured - it needs `sla_user`, whose effective amber/red thresholds
    rank breached/red first, then amber, then the rest, oldest-first
    within each tier - but is no longer offered as a stored preference.

    `track_filter` accepts a QueueTrackFilter value and narrows to courses
    whose category matches; NONE returns an empty queue by design.
    """

    statuses = status_in or [CourseStatus.SUBMITTED, CourseStatus.IN_REVIEW]
    queryset = (
        Course.objects.filter(status__in=statuses)
        .select_related("category", "topic", "creator")
        .prefetch_related(
            "review_assignments__reviewer",
            "review_actions__reviewer",
            "quality_check_runs",
            "quality_findings",
            "distribution_channels",
        )
    )

    # A reviewer who turned every track off asked for an empty queue;
    # honour it rather than quietly showing them everything.
    if track_filter == QueueTrackFilter.NONE:
        return queryset.none()

    category_track_preference = QUEUE_TRACK_FILTER_TO_CATEGORY_TRACK_PREFERENCE.get(
        track_filter
    )
    if category_track_preference is not None:
        queryset = queryset.filter(category__track_preference=category_track_preference)

    # The date-scoped views narrow to a recent window, then sort oldest
    # first - they are a filter the design presents inside the same
    # dropdown as the orderings.
    window_days = QUEUE_SORT_WINDOW_DAYS.get(sort_order)
    if window_days is not None:
        queryset = queryset.filter(
            submitted_at__gte=timezone.now() - timedelta(days=window_days)
        )
        return queryset.order_by("submitted_at")

    if sort_order == "NEWEST_FIRST":
        return queryset.order_by("-submitted_at")

    if sort_order == "SLA_URGENCY" and sla_user is not None:
        amber_hours, red_hours = sla_threshold_service.get_effective_thresholds(
            user=sla_user
        )
        queryset = queryset.annotate(
            queue_age=ExpressionWrapper(
                timezone.now() - F("submitted_at"), output_field=DurationField()
            )
        ).annotate(
            urgency_tier=Case(
                When(queue_age__gte=timedelta(hours=red_hours), then=Value(0)),
                When(queue_age__gte=timedelta(hours=amber_hours), then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
        return queryset.order_by("urgency_tier", "submitted_at")

    return queryset.order_by("submitted_at")
