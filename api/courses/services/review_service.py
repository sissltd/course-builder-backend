from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.services import activity_service
from api.courses.enums import CourseStatus, ReviewActionType, ReviewStage
from api.courses.models import Course, ReviewAction, ReviewAssignment
from api.courses.services import quality_review_service
from api.notification.models import Notification
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
            stage=ReviewStage.CONTENT,
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
            stage=ReviewStage.CONTENT,
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


def approve_content(
    *, course: Course, reviewer: User, feedback: dict | None = None
) -> ReviewAction:
    """Complete content review and move a course to mandatory QA verification."""

    require_role(
        reviewer, IsCreatorReviewerRole.allowed_roles + IsAdminRole.allowed_roles
    )
    if course.status not in REVIEWABLE_STATUSES:
        raise exceptions.ValidationError(
            f"Course cannot be content-approved from status '{course.status}'."
        )
    reviewer_availability_service.require_reviewer_available(user=reviewer)
    with transaction.atomic():
        if not course.quality_check_runs.exists():
            quality_review_service.run_baseline_checks(course=course)
        action = ReviewAction.objects.create(
            course=course,
            reviewer=reviewer,
            action=ReviewActionType.APPROVE,
            stage=ReviewStage.CONTENT,
            feedback=feedback or {},
        )
        course.status = CourseStatus.QA_VERIFICATION
        course.updated_by = reviewer
        course.save(update_fields=["status", "updated_by", "updated_datetime"])
        assignment, _ = ReviewAssignment.objects.get_or_create(
            course=course, stage=ReviewStage.CONTENT
        )
        assignment.reviewer = reviewer
        assignment.claimed_at = assignment.claimed_at or timezone.now()
        assignment.completed_at = timezone.now()
        assignment.save()
        activity_service.log_activity(
            user=reviewer,
            category=UserActivityCategoryEnums.APPROVAL,
            action=UserActivityActionEnums.COURSE_APPROVED,
            summary=f"You approved '{course.title}' for QA verification.",
            target=course,
        )
        if course.creator_id:
            Notification.emit_in_app_notification(
                receivers=[course.creator],
                title="Course passed content review",
                content=f"Your course '{course.title}' is now awaiting media QA verification.",
                metadata={"course_id": course.id, "stage": ReviewStage.CONTENT},
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
        if course.source == "CREATOR":
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
