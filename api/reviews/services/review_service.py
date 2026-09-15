from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.services import activity_service
from api.courses.enums import CourseSourceType, CourseStatus
from api.courses.models import Course
from api.notification.models import Notification
from api.reviews.enums import ReviewActionType, ReviewStage
from api.reviews.models import ReviewAction, ReviewAssignment
from api.reviews.services import quality_review_service
from api.users.enums import (
    UserActivityActionEnums,
    UserActivityCategoryEnums,
    UserRole,
)
from api.users.models import User
from api.users.permissions import IsAdminRole, IsCreatorReviewerRole, require_role
from api.users.services import reviewer_availability_service
from api.wallet.services import wallet_service

REVIEWABLE_STATUSES = (CourseStatus.SUBMITTED, CourseStatus.IN_REVIEW)

#: Who may take each content seat, in the order a submission passes through
#: them. The Admin tier may take any seat on top of this (is_admin_tier).
#: QA is not a content seat; it keeps its own claim/approve/reject below.
SEAT_ROLES = {
    ReviewStage.CONTENT: (UserRole.CREATOR_REVIEWER,),
    ReviewStage.SECOND_REVIEW: (UserRole.CREATOR_REVIEWER,),
    ReviewStage.VERIFICATION: (UserRole.STAFF_VERIFIER,),
}
CONTENT_REVIEW_SEATS = tuple(SEAT_ROLES)

#: The seat an approval hands the course to, derived from the order above so
#: the two can't disagree. The last seat maps to None: its approval ends
#: content review and the course moves on to QA verification.
NEXT_SEAT = dict(zip(CONTENT_REVIEW_SEATS, CONTENT_REVIEW_SEATS[1:] + (None,)))


def is_admin_tier(user: User) -> bool:
    """Admin, Approver and Super Admin may take any content seat, and may
    decide one they don't hold. Superusers count as Admin tier, matching
    require_role's bypass."""

    return bool(user.is_superuser or user.role in IsAdminRole.allowed_roles)


def claimable_seats(*, user: User) -> tuple:
    """The content seats `user`'s role may take, in chain order."""

    if is_admin_tier(user):
        return CONTENT_REVIEW_SEATS
    return tuple(seat for seat, roles in SEAT_ROLES.items() if user.role in roles)


def decided_seats(*, user: User) -> QuerySet[ReviewAssignment]:
    """Content seats `user` has completed, on any course.

    Every cycle starts with its seats cleared (course_service.
    start_review_cycle), so a completed row always belongs to its course's
    current cycle - or to the one a rejection just ended, whose course
    sits in Draft until a resubmission clears it.
    """

    return ReviewAssignment.objects.filter(
        reviewer=user, stage__in=CONTENT_REVIEW_SEATS, completed_at__isnull=False
    )


def open_seat(*, course: Course, verb: str) -> str:
    """Return the content seat `course` is at, or 400 when it isn't at one.

    `verb` completes the refusal ("claimed", "rejected", ...) so each
    action keeps the wording it has always used.

    A course in review with no seat recorded is read as First Review, the
    same fallback the queue and the course serializer apply: it has not
    started the chain, so it starts at the beginning rather than being
    undecidable.
    """

    seat = course.review_stage or ReviewStage.CONTENT
    if course.status not in REVIEWABLE_STATUSES or seat not in SEAT_ROLES:
        raise exceptions.ValidationError(
            f"Course cannot be {verb} from status '{course.status}'."
        )
    return seat


def require_seat_access(*, course: Course, seat: str, user: User) -> None:
    """Refuse (403) a `user` who may not hold `seat` on `course` this cycle.

    First the seat's role, which the Admin tier always passes. Then four
    eyes, which it doesn't: whoever decided an earlier seat of this cycle
    cannot take a later one.
    """

    label = ReviewStage(seat).label
    if seat not in claimable_seats(user=user):
        roles = " or ".join(role.label for role in SEAT_ROLES[seat])
        raise exceptions.PermissionDenied(f"The {label} seat is for a {roles}.")

    earlier_seats = CONTENT_REVIEW_SEATS[: CONTENT_REVIEW_SEATS.index(seat)]
    if (
        earlier_seats
        and decided_seats(user=user)
        .filter(course=course, stage__in=earlier_seats)
        .exists()
    ):
        raise exceptions.PermissionDenied(
            "You decided an earlier seat on this course, so a different "
            f"reviewer must take the {label} seat."
        )


def _lock_open_seat(*, course: Course, verb: str) -> tuple[Course, str]:
    """Lock `course`'s row and return the fresh row with the seat it is at.

    Runs inside the caller's transaction. Status and seat are checked on
    the locked row, so of two simultaneous decisions the second sees the
    first's result. `course` is the instance the caller loaded: if the
    locked row has moved on since, the caller would be deciding a seat they
    never looked at - and an admin would even pass four eyes on it - so
    that is refused too.
    """

    locked = Course.objects.select_for_update().get(pk=course.pk)
    seat = open_seat(course=locked, verb=verb)
    if (locked.status, locked.review_stage) != (course.status, course.review_stage):
        raise exceptions.ValidationError(
            "This course moved on while you were reviewing it. "
            "Reload it and try again."
        )
    return locked, seat


def _hold_seat(*, course: Course, seat: str, reviewer: User) -> ReviewAssignment:
    """Return `seat`'s assignment once `reviewer` is entitled to decide it.

    Only the claimant decides; anyone else in the reviewer pool is told to
    claim first (400) or that the seat is someone else's (403). The Admin
    tier may decide a seat another reviewer holds, or one nobody claimed:
    the seat moves to them, and the takeover is logged with whoever held it
    before so an override is never silent.
    """

    require_seat_access(course=course, seat=seat, user=reviewer)
    assignment, _ = ReviewAssignment.objects.get_or_create(course=course, stage=seat)
    # Submitted means nobody holds the seat, whatever the row still says.
    holder_id = (
        assignment.reviewer_id if course.status == CourseStatus.IN_REVIEW else None
    )
    if holder_id == reviewer.id:
        return assignment
    if not is_admin_tier(reviewer):
        if holder_id is None:
            raise exceptions.ValidationError("Claim this course first.")
        raise exceptions.PermissionDenied(
            "Only the reviewer who claimed this course can decide it."
        )

    holder = assignment.reviewer if holder_id else None
    label = ReviewStage(seat).label
    assignment.reviewer = reviewer
    assignment.claimed_at = timezone.now()
    assignment.save(update_fields=["reviewer", "claimed_at", "updated_datetime"])
    activity_service.log_activity(
        user=reviewer,
        category=UserActivityCategoryEnums.APPROVAL,
        action=UserActivityActionEnums.COURSE_ASSIGNED,
        summary=(
            f"You took the {label} seat on '{course.title}' over from {holder.email}."
            if holder
            else f"You took the unclaimed {label} seat on '{course.title}'."
        ),
        details={
            "override": True,
            "seat": seat,
            "previous_reviewer_id": str(holder.id) if holder else None,
        },
        target=course,
    )
    return assignment


def approve_course(
    *, course: Course, reviewer: User, feedback: dict | None = None
) -> ReviewAction:
    """Approve a course under review.

    Raises ValidationError if the course is not Submitted/In Review - this is
    what prevents double-approval (an already Approved/Published/Draft course
    cannot be approved again). On success: records a ReviewAction, transitions
    the course to Approved, credits creator-uploaded work with the price
    snapshotted at submission, and notifies the creator. AI-generated courses
    are approved without a payout. All changes are atomic.
    """

    require_role(
        reviewer, IsCreatorReviewerRole.allowed_roles + IsAdminRole.allowed_roles
    )
    if course.status not in REVIEWABLE_STATUSES:
        raise exceptions.ValidationError(
            f"Course cannot be approved from status '{course.status}'."
        )
    reviewer_availability_service.require_reviewer_available(user=reviewer)

    with transaction.atomic():
        review_action = ReviewAction.objects.create(
            course=course,
            reviewer=reviewer,
            action=ReviewActionType.APPROVE,
            feedback=feedback or {},
            stage=ReviewStage.CONTENT,
        )
        course.status = CourseStatus.APPROVED
        course.approved_at = timezone.now()
        course.updated_by = reviewer
        course.save(
            update_fields=["status", "approved_at", "updated_by", "updated_datetime"]
        )

        if course.source_type == CourseSourceType.CREATOR_UPLOADED:
            wallet_service.credit_wallet(
                user=course.creator,
                amount=course.creator_price_snapshot,
                course=course,
                description=f"Course '{course.title}' approved",
            )

        Notification.emit_in_app_notification(
            receivers=[course.creator],
            title="Course approved",
            content=(
                f"Your course '{course.title}' has been approved and your wallet has been credited."
                if course.source_type == CourseSourceType.CREATOR_UPLOADED
                else f"Your AI-generated course '{course.title}' has been approved."
            ),
            metadata={
                "course_id": course.id,
                "amount": (
                    course.creator_price_snapshot
                    if course.source_type == CourseSourceType.CREATOR_UPLOADED
                    else None
                ),
            },
        )
        activity_service.log_activity(
            user=reviewer,
            category=UserActivityCategoryEnums.APPROVAL,
            action=UserActivityActionEnums.COURSE_APPROVED,
            summary=f"You approved '{course.title}'.",
            target=course,
        )

    return review_action


def reject_course(
    *,
    course: Course,
    reviewer: User,
    feedback: dict,
    flags: list[dict] | None = None,
) -> ReviewAction:
    """Reject the content seat a course is at and send it back to Draft.

    A rejection at any seat ends the cycle. Per PRD "Returns to Draft.
    Creator revises." the course status reverts directly to Draft
    (CourseStatus.REJECTED is never persisted on Course.status - the
    rejection itself is preserved via the ReviewAction record and
    Course.rejected_at), and resubmitting restarts the chain at First
    Review with every seat cleared. The claimant, four-eyes, lock and
    stale-course rules are approve_content's.

    Requires a non-empty feedback["summary"]. `flags`, when supplied, is a
    list of structured issue dicts (flag_type, title, system_message,
    reviewer_note, optional lesson_id/module_id) persisted as ReviewFlag
    rows attached to the recorded ReviewAction - what the creator
    dashboard's "Course details" panel renders item by item. Notifies the
    creator with the feedback.
    """

    require_role(
        reviewer, IsCreatorReviewerRole.allowed_roles + IsAdminRole.allowed_roles
    )
    if not (feedback or {}).get("summary"):
        raise exceptions.ValidationError(
            {"feedback": "A summary is required when rejecting a course."}
        )

    with transaction.atomic():
        course, seat = _lock_open_seat(course=course, verb="rejected")
        assignment = _hold_seat(course=course, seat=seat, reviewer=reviewer)
        reviewer_availability_service.require_reviewer_available(user=reviewer)
        review_action = ReviewAction.objects.create(
            course=course,
            reviewer=reviewer,
            action=ReviewActionType.REJECT,
            feedback=feedback,
            stage=seat,
        )
        _create_review_flags(review_action=review_action, flags=flags or [])
        now = timezone.now()
        assignment.completed_at = now
        assignment.save(update_fields=["completed_at", "updated_datetime"])
        course.status = CourseStatus.DRAFT
        course.review_stage = ""
        course.rejected_at = now
        course.updated_by = reviewer
        course.save(
            update_fields=[
                "status",
                "review_stage",
                "rejected_at",
                "updated_by",
                "updated_datetime",
            ]
        )

        Notification.emit_in_app_notification(
            receivers=[course.creator],
            title="Course rejected",
            content=f"Your course '{course.title}' was rejected and returned to Draft for revision.",
            metadata={"course_id": course.id, "feedback": feedback},
        )
        activity_service.log_activity(
            user=reviewer,
            category=UserActivityCategoryEnums.APPROVAL,
            action=UserActivityActionEnums.COURSE_REJECTED,
            summary=f"You rejected '{course.title}'.",
            target=course,
        )

    return review_action


def _create_review_flags(*, review_action: ReviewAction, flags: list[dict]) -> None:
    """Persist structured issue rows for a rejection.

    Each flag dict is validated for required keys (flag_type, title) and
    optional lesson/module ids; anything failing validation aborts the
    whole rejection transaction - partial flag sets would misrepresent
    the reviewer's decision.
    """

    from api.courses.models import Lesson, Module
    from api.reviews.models import ReviewFlag

    rows = []
    for flag in flags:
        if not flag.get("flag_type") or not flag.get("title"):
            raise exceptions.ValidationError(
                {
                    "flags": "Each flag requires 'flag_type' and 'title'.",
                }
            )
        lesson = (
            Lesson.objects.filter(
                pk=flag.get("lesson_id"), module__course=review_action.course
            ).first()
            if flag.get("lesson_id")
            else None
        )
        if flag.get("lesson_id") and lesson is None:
            raise exceptions.ValidationError(
                {"flags": "flag lesson_id must belong to the reviewed course."}
            )
        module = (
            Module.objects.filter(
                pk=flag.get("module_id"), course=review_action.course
            ).first()
            if flag.get("module_id")
            else None
        )
        if flag.get("module_id") and module is None:
            raise exceptions.ValidationError(
                {"flags": "flag module_id must belong to the reviewed course."}
            )
        rows.append(
            ReviewFlag(
                review_action=review_action,
                lesson=lesson,
                module=module,
                flag_type=flag["flag_type"],
                title=flag["title"],
                system_message=flag.get("system_message", ""),
                reviewer_note=flag.get("reviewer_note", ""),
            )
        )
    if rows:
        ReviewFlag.objects.bulk_create(rows)


# --- Two-stage review flow (content review -> QA verification) ---------
# Ported from upstream; complements the single-stage functions above.


def approve_content(
    *, course: Course, reviewer: User, feedback: dict | None = None
) -> ReviewAction:
    """Approve the content seat a course is at and hand it to the next one.

    Three seats decide content, in order (SEAT_ROLES): First Review, Second
    Review, Verification. Each approval completes its own seat and returns
    the course to the pending queue at the next one; only the last seat ends
    content review and moves the course to mandatory QA verification. The
    creator hears once, at that point, rather than after every seat.

    The claimant, four-eyes, lock and stale-course rules are reject_course's.
    """

    require_role(
        reviewer, IsCreatorReviewerRole.allowed_roles + IsAdminRole.allowed_roles
    )
    with transaction.atomic():
        course, seat = _lock_open_seat(course=course, verb="content-approved")
        assignment = _hold_seat(course=course, seat=seat, reviewer=reviewer)
        reviewer_availability_service.require_reviewer_available(user=reviewer)
        if not course.quality_check_runs.exists():
            quality_review_service.run_baseline_checks(course=course)
        action = ReviewAction.objects.create(
            course=course,
            reviewer=reviewer,
            action=ReviewActionType.APPROVE,
            stage=seat,
            feedback=feedback or {},
        )
        assignment.completed_at = timezone.now()
        assignment.save(update_fields=["completed_at", "updated_datetime"])

        # The last seat has no successor, which is what ends content review.
        next_seat = NEXT_SEAT[seat]
        course.status = (
            CourseStatus.SUBMITTED if next_seat else CourseStatus.QA_VERIFICATION
        )
        course.review_stage = next_seat or ""
        course.updated_by = reviewer
        course.save(
            update_fields=["status", "review_stage", "updated_by", "updated_datetime"]
        )
        activity_service.log_activity(
            user=reviewer,
            category=UserActivityCategoryEnums.APPROVAL,
            action=UserActivityActionEnums.COURSE_APPROVED,
            summary=f"You approved '{course.title}' at {ReviewStage(seat).label}.",
            target=course,
        )
        if not next_seat and course.creator_id:
            Notification.emit_in_app_notification(
                receivers=[course.creator],
                title="Course passed content review",
                content=f"Your course '{course.title}' is now awaiting media QA verification.",
                metadata={"course_id": course.id, "stage": seat},
            )
    return action


def claim_qa_verification(*, course: Course, reviewer: User) -> Course:
    from api.users.permissions import IsQaReviewerRole

    require_role(reviewer, IsQaReviewerRole.allowed_roles + IsAdminRole.allowed_roles)
    if course.status != CourseStatus.QA_VERIFICATION:
        raise exceptions.ValidationError(
            f"Course cannot enter QA from status '{course.status}'."
        )
    reviewer_availability_service.require_reviewer_available(user=reviewer)
    assignment, _ = ReviewAssignment.objects.get_or_create(
        course=course, stage=ReviewStage.QA
    )
    if assignment.reviewer_id and assignment.reviewer_id != reviewer.id:
        raise exceptions.ValidationError(
            "This course is already assigned to another QA reviewer."
        )
    assignment.reviewer = reviewer
    assignment.claimed_at = assignment.claimed_at or timezone.now()
    assignment.save(update_fields=["reviewer", "claimed_at", "updated_datetime"])
    return course


def approve_qa(
    *, course: Course, reviewer: User, feedback: dict | None = None
) -> ReviewAction:
    from api.users.permissions import IsQaReviewerRole

    require_role(reviewer, IsQaReviewerRole.allowed_roles + IsAdminRole.allowed_roles)
    if course.status != CourseStatus.QA_VERIFICATION:
        raise exceptions.ValidationError(
            f"Course cannot be QA-approved from status '{course.status}'."
        )
    failures = quality_review_service.required_media_failures(course=course)
    if failures:
        raise exceptions.ValidationError({"qa_verification": failures})
    reviewer_availability_service.require_reviewer_available(user=reviewer)
    with transaction.atomic():
        action = ReviewAction.objects.create(
            course=course,
            reviewer=reviewer,
            action=ReviewActionType.APPROVE,
            stage=ReviewStage.QA,
            feedback=feedback or {},
        )
        course.status = CourseStatus.APPROVED
        course.approved_at = timezone.now()
        course.updated_by = reviewer
        course.save(
            update_fields=["status", "approved_at", "updated_by", "updated_datetime"]
        )
        assignment, _ = ReviewAssignment.objects.get_or_create(
            course=course, stage=ReviewStage.QA
        )
        assignment.reviewer = reviewer
        assignment.claimed_at = assignment.claimed_at or timezone.now()
        assignment.completed_at = timezone.now()
        assignment.save()
        if course.source_type == CourseSourceType.CREATOR_UPLOADED:
            wallet_service.credit_wallet(
                user=course.creator,
                amount=course.creator_price_snapshot,
                course=course,
                description=f"Course '{course.title}' approved after QA verification",
            )
            Notification.emit_in_app_notification(
                receivers=[course.creator],
                title="Course approved",
                content=f"Your course '{course.title}' passed QA verification and has been approved.",
                metadata={
                    "course_id": course.id,
                    "amount": course.creator_price_snapshot,
                },
            )
        activity_service.log_activity(
            user=reviewer,
            category=UserActivityCategoryEnums.APPROVAL,
            action=UserActivityActionEnums.COURSE_APPROVED,
            summary=f"You QA-approved '{course.title}'.",
            target=course,
        )
    return action


def reject_qa(*, course: Course, reviewer: User, feedback: dict) -> ReviewAction:
    from api.users.permissions import IsQaReviewerRole

    require_role(reviewer, IsQaReviewerRole.allowed_roles + IsAdminRole.allowed_roles)
    if not feedback.get("summary"):
        raise exceptions.ValidationError(
            {"feedback": "A summary is required when rejecting a course."}
        )
    if course.status != CourseStatus.QA_VERIFICATION:
        raise exceptions.ValidationError(
            f"Course cannot be QA-rejected from status '{course.status}'."
        )
    with transaction.atomic():
        action = ReviewAction.objects.create(
            course=course,
            reviewer=reviewer,
            action=ReviewActionType.REJECT,
            stage=ReviewStage.QA,
            feedback=feedback,
        )
        course.status = CourseStatus.DRAFT
        course.rejected_at = timezone.now()
        course.updated_by = reviewer
        course.save(
            update_fields=["status", "rejected_at", "updated_by", "updated_datetime"]
        )
        if course.creator_id:
            Notification.emit_in_app_notification(
                receivers=[course.creator],
                title="Course rejected in QA",
                content=f"Your course '{course.title}' requires media or accessibility fixes.",
                metadata={
                    "course_id": course.id,
                    "feedback": feedback,
                    "stage": ReviewStage.QA,
                },
            )
        activity_service.log_activity(
            user=reviewer,
            category=UserActivityCategoryEnums.APPROVAL,
            action=UserActivityActionEnums.COURSE_REJECTED,
            summary=f"You QA-rejected '{course.title}'.",
            target=course,
        )
    return action
