from datetime import timedelta

from django.db import transaction
from django.db.models import (
    Case,
    DurationField,
    ExpressionWrapper,
    F,
    IntegerField,
    Q,
    QuerySet,
    Value,
    When,
)
from django.utils import timezone
from rest_framework import exceptions

from api.achievements.enums import BadgeCriterion
from api.authorization import codenames
from api.achievements.services import award_service
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
    review_service,
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
from api.authorization.services import permission_service
from api.users.services import reviewer_availability_service

def _category_track_q(track_filter) -> Q | None:
    """Category-level TrackPreference constraint for a reviewer's
    QueueTrackFilter preference. ALL means "no filter" (None).

    OPEN categories belong to both tracks: a category marked open to every
    track is exactly where a creator course would otherwise vanish under a
    single-track filter, so each single track matches its own preferred
    categories plus OPEN ones.
    """

    if track_filter == QueueTrackFilter.CREATOR_TRACK:
        return Q(
            category__track_preference__in=(
                TrackPreference.CREATOR_PREFERRED,
                TrackPreference.OPEN,
            )
        )
    if track_filter == QueueTrackFilter.AI_TRACK:
        return Q(
            category__track_preference__in=(
                TrackPreference.AI_PREFERRED,
                TrackPreference.OPEN,
            )
        )
    return None

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

    permission_service.require_permission(creator, codenames.COURSES_CREATE)
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
        award_service.schedule_evaluation(
            creator_id=creator.id, criterion=BadgeCriterion.COURSES_CREATED
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

    permission_service.require_any_permission(
        actor, (codenames.COURSES_CREATE, codenames.COURSES_EDIT)
    )
    if course.creator_id != actor.id:
        raise exceptions.ValidationError(
            "Only the course creator can submit this course."
        )
    if course.status != CourseStatus.DRAFT:
        raise exceptions.ValidationError(
            f"Course cannot be submitted from status '{course.status}'."
        )

    hold_hours = platform_settings_service.get_settings().draft_minimum_hold_hours
    if hold_hours and course.draft_started_at:
        releases_at = course.draft_started_at + timedelta(hours=hold_hours)
        if timezone.now() < releases_at:
            raise exceptions.ValidationError(
                f"This course must stay in draft for {hold_hours} hours. It "
                f"can be submitted from {releases_at:%Y-%m-%d %H:%M} UTC."
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
        course.submitted_at = timezone.now()
        course.updated_by = actor
        course.save(
            update_fields=[
                "creator_price_snapshot",
                "submitted_at",
                "updated_by",
                "updated_datetime",
            ]
        )
        start_review_cycle(course=course)
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


def start_review_cycle(*, course: Course) -> Course:
    """Send `course` to the pending queue at First Review, every seat empty.

    Both ways into review call this - submit_course and an approved appeal
    (course_appeal_service.approve_appeal) - so no cycle inherits the last
    one's claimants or completion stamps. Four eyes depends on that: it
    reads every completed seat it finds as part of the current cycle. QA's
    row is cleared too, otherwise a resubmitted course would still be
    "assigned to another QA reviewer" from its previous pass.
    """

    with transaction.atomic():
        course.status = CourseStatus.SUBMITTED
        course.review_stage = ReviewStage.CONTENT
        # A fresh cycle starts un-alerted and unflagged, for the same reason
        # the seats below are cleared: nothing from the last pass carries over.
        course.sla_red_alerted_at = None
        course.flagged_at = None
        course.flag_reason = ""
        course.save(
            update_fields=[
                "status",
                "review_stage",
                "sla_red_alerted_at",
                "flagged_at",
                "flag_reason",
                "updated_datetime",
            ]
        )
        ReviewAssignment.objects.filter(course=course).update(
            reviewer=None,
            claimed_at=None,
            completed_at=None,
            updated_datetime=timezone.now(),
        )
    return course


def claim_for_review(*, course: Course, reviewer: User) -> Course:
    """Claim the content seat a Submitted course is waiting at.

    The seat decides who may claim it (review_service.SEAT_ROLES): First
    and Second Review take a Creator Reviewer, Verification takes a
    Verifier, and the Admin tier may take any seat. Four eyes applies too -
    whoever decided an earlier seat of this cycle cannot claim a later one.
    Both refusals are PermissionDenied.

    Idempotent for the seat's holder. A row lock makes simultaneous claims
    exclusive; a different reviewer receives a validation error.
    Raises ValidationError for any other status. A reviewer marked
    Unavailable cannot make a *new* claim (checked after the idempotent
    short-circuit, so re-calling claim on a course they already hold still
    works even if they've since gone Unavailable).
    """

    permission_service.require_any_permission(
        reviewer, (codenames.COURSES_APPROVE, codenames.COURSES_REJECT)
    )
    with transaction.atomic():
        course = Course.objects.select_for_update().get(pk=course.pk)
        seat = review_service.open_seat(course=course, verb="claimed")
        review_service.require_seat_access(course=course, seat=seat, user=reviewer)
        assignment = ReviewAssignment.objects.filter(course=course, stage=seat).first()
        if course.status == CourseStatus.IN_REVIEW:
            if assignment and assignment.reviewer_id == reviewer.id:
                return course
            raise exceptions.ValidationError(
                "This course is already assigned to another reviewer."
            )
        reviewer_availability_service.require_reviewer_available(user=reviewer)

        course.status = CourseStatus.IN_REVIEW
        course.save(update_fields=["status", "updated_datetime"])
        assignment = assignment or ReviewAssignment(course=course, stage=seat)
        assignment.reviewer = reviewer
        # A Submitted seat is never held (start_review_cycle clears it), so
        # this is always a fresh claim.
        assignment.claimed_at = timezone.now()
        assignment.save()
    activity_service.log_activity(
        user=reviewer,
        category=UserActivityCategoryEnums.COURSE,
        action=UserActivityActionEnums.COURSE_ASSIGNED,
        summary=(
            f"Course '{course.title}' assigned to you for "
            f"{ReviewStage(seat).label}."
        ),
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

    permission_service.require_permission(actor, codenames.COURSES_PUBLISH)
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
        if course.creator_id:
            award_service.schedule_evaluation(
                creator_id=course.creator_id, criterion=BadgeCriterion.COURSES_PUBLISHED
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
    seats_for: User | None = None,
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

    `seats_for` narrows to the content seats that user can take right now:
    the seats their role holds (review_service.SEAT_ROLES), minus courses
    where four eyes locks them out because they decided an earlier seat of
    the current cycle. The Admin tier may take any seat, so it isn't
    narrowed. The Pending screen passes it; the other screens list every
    course in their status.
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

    # Both conditions ride in the same SQL (the lockout is a subquery), so
    # the queue's query count doesn't grow with the rows it returns.
    if seats_for is not None and not review_service.is_admin_tier(seats_for):
        seats = review_service.claimable_seats(user=seats_for)
        at_a_claimable_seat = Q(review_stage__in=seats)
        if ReviewStage.CONTENT in seats:
            # A course sitting in review with no seat recorded has not started
            # the chain. It belongs at First Review rather than nowhere -
            # without this it would be invisible to every reviewer, which is
            # also the fallback the course serializer reports for it.
            at_a_claimable_seat |= Q(review_stage="")
        queryset = queryset.filter(at_a_claimable_seat).exclude(
            pk__in=review_service.decided_seats(user=seats_for).values("course_id")
        )

    # A reviewer who turned every track off asked for an empty queue;
    # honour it rather than quietly showing them everything.
    if track_filter == QueueTrackFilter.NONE:
        return queryset.none()

    track_q = _category_track_q(track_filter)
    if track_q is not None:
        queryset = queryset.filter(track_q)

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
