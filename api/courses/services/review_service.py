from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.services import activity_service
from api.courses.enums import CourseStatus, ReviewActionType
from api.courses.models import Course, ReviewAction
from api.notification.models import Notification
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.models import User
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


def reject_course(*, course: Course, reviewer: User, feedback: dict) -> ReviewAction:
    """Reject a course under review.

    Requires a non-empty feedback["summary"]. On success: records a
    ReviewAction, and per PRD "Returns to Draft. Creator revises." the course
    status reverts directly to Draft (CourseStatus.REJECTED is never persisted
    on Course.status - the rejection itself is preserved via the ReviewAction
    record and Course.rejected_at). Notifies the creator with the feedback.
    """

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
