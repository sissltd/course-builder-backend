from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.services import activity_service
from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.notification.models import Notification
from api.reviews.enums import ReviewActionType
from api.reviews.models import ReviewAction
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.models import User
from api.users.permissions import IsAdminRole, IsCreatorReviewerRole, require_role
from api.users.services import reviewer_availability_service
from api.wallet.services import wallet_service

REVIEWABLE_STATUSES = (CourseStatus.SUBMITTED, CourseStatus.IN_REVIEW)


def approve_course(
    *, course: Course, reviewer: User, feedback: dict | None = None
) -> ReviewAction:
    """Approve a course under review.

    Raises ValidationError if the course is not Submitted/In Review - this is
    what prevents double-approval (an already Approved/Published/Draft course
    cannot be approved again). On success: records a ReviewAction, transitions
    the course to Approved, credits the creator's wallet with the price
    snapshotted at submission, and notifies the creator. All in one atomic
    transaction so a failure partway through leaves nothing half-applied.
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
        )
        course.status = CourseStatus.APPROVED
        course.approved_at = timezone.now()
        course.updated_by = reviewer
        course.save(
            update_fields=["status", "approved_at", "updated_by", "updated_datetime"]
        )

        wallet_service.credit_wallet(
            user=course.creator,
            amount=course.creator_price_snapshot,
            course=course,
            description=f"Course '{course.title}' approved",
        )

        Notification.emit_in_app_notification(
            receivers=[course.creator],
            title="Course approved",
            content=f"Your course '{course.title}' has been approved and your wallet has been credited.",
            metadata={"course_id": course.id, "amount": course.creator_price_snapshot},
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
    """Reject a course under review.

    Requires a non-empty feedback["summary"]. `flags`, when supplied, is a
    list of structured issue dicts (flag_type, title, system_message,
    reviewer_note, optional lesson_id/module_id) persisted as ReviewFlag
    rows attached to the recorded ReviewAction - what the creator
    dashboard's "Course details" panel renders item by item. On success:
    records the ReviewAction (with flags), and per PRD "Returns to Draft.
    Creator revises." the course status reverts directly to Draft
    (CourseStatus.REJECTED is never persisted on Course.status - the
    rejection itself is preserved via the ReviewAction record and
    Course.rejected_at). Notifies the creator with the feedback.
    """

    require_role(
        reviewer, IsCreatorReviewerRole.allowed_roles + IsAdminRole.allowed_roles
    )
    if not (feedback or {}).get("summary"):
        raise exceptions.ValidationError(
            {"feedback": "A summary is required when rejecting a course."}
        )
    if course.status not in REVIEWABLE_STATUSES:
        raise exceptions.ValidationError(
            f"Course cannot be rejected from status '{course.status}'."
        )
    reviewer_availability_service.require_reviewer_available(user=reviewer)

    with transaction.atomic():
        review_action = ReviewAction.objects.create(
            course=course,
            reviewer=reviewer,
            action=ReviewActionType.REJECT,
            feedback=feedback,
        )
        _create_review_flags(review_action=review_action, flags=flags or [])
        course.status = CourseStatus.DRAFT
        course.rejected_at = timezone.now()
        course.updated_by = reviewer
        course.save(
            update_fields=["status", "rejected_at", "updated_by", "updated_datetime"]
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
            Lesson.objects.filter(pk=flag.get("lesson_id"), module__course=review_action.course).first()
            if flag.get("lesson_id")
            else None
        )
        if flag.get("lesson_id") and lesson is None:
            raise exceptions.ValidationError(
                {"flags": "flag lesson_id must belong to the reviewed course."}
            )
        module = (
            Module.objects.filter(pk=flag.get("module_id"), course=review_action.course).first()
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
