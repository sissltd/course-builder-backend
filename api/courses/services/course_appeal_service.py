from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.services import activity_service
from api.courses.enums import AppealStatus, CourseStatus
from api.courses.models import Course, CourseAppeal
from api.notification.models import Notification
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.models import User
from api.users.permissions import (
    IsAdminOrSuperAdminRole,
    IsCourseCreatorRole,
    require_role,
)


def submit_appeal(
    *,
    user: User,
    course: Course,
    title: str,
    email: str,
    description: str,
    web_link: str = "",
) -> CourseAppeal:
    """File a Pending appeal against a rejected course and notify Admins/
    Super Admins (PRD Section 12: "Creator disputes rejection... Creator
    submits written dispute through platform...").

    Only the creator who owns the course may appeal it, and only while it's
    sitting in the just-rejected state (Course.rejected_at set, status back
    at DRAFT - see review_service.reject_course). A second appeal can't be
    opened while one is still Pending, mirroring the single-open-request
    idiom used by TopicReservationRequest.
    """

    require_role(user, IsCourseCreatorRole.allowed_roles)
    if course.creator_id != user.id:
        raise exceptions.PermissionDenied("You can only appeal your own course.")
    if course.status != CourseStatus.DRAFT or course.rejected_at is None:
        raise exceptions.ValidationError("This course has not been rejected.")
    if CourseAppeal.objects.filter(course=course, status=AppealStatus.PENDING).exists():
        raise exceptions.ValidationError(
            "An appeal for this course is already pending review."
        )

    appeal = CourseAppeal.objects.create(
        course=course,
        submitted_by=user,
        title=title,
        email=email,
        web_link=web_link,
        description=description,
    )

    admins = list(User.objects.filter(role__in=IsAdminOrSuperAdminRole.allowed_roles))
    if admins:
        Notification.emit_in_app_notification(
            receivers=admins,
            title="New course-rejection appeal",
            content=f"{user.email} appealed the rejection of '{course.title}'.",
            metadata={"course_appeal_id": appeal.id, "course_id": course.id},
        )
    activity_service.log_activity(
        user=user,
        category=UserActivityCategoryEnums.APPROVAL,
        action=UserActivityActionEnums.APPEAL_SUBMITTED,
        summary=f"You appealed the rejection of '{course.title}'.",
        target=course,
    )

    return appeal


def approve_appeal(
    *, appeal: CourseAppeal, actor: User, notes: str = ""
) -> CourseAppeal:
    """Approve a Pending appeal: reopen the course for review (status ->
    SUBMITTED) and notify the creator. Decision is final per the PRD - once
    decided, this appeal can't be re-decided."""

    require_role(actor, IsAdminOrSuperAdminRole.allowed_roles)
    if appeal.status != AppealStatus.PENDING:
        raise exceptions.ValidationError(
            f"Appeal cannot be approved from status '{appeal.status}'."
        )

    with transaction.atomic():
        appeal.status = AppealStatus.APPROVED
        appeal.decision_notes = notes
        appeal.reviewed_by = actor
        appeal.reviewed_at = timezone.now()
        appeal.save(
            update_fields=[
                "status",
                "decision_notes",
                "reviewed_by",
                "reviewed_at",
                "updated_datetime",
            ]
        )

        course = appeal.course
        course.status = CourseStatus.SUBMITTED
        course.updated_by = actor
        course.save(update_fields=["status", "updated_by", "updated_datetime"])

        Notification.emit_in_app_notification(
            receivers=[appeal.submitted_by],
            title="Appeal approved",
            content=f"Your appeal for '{course.title}' was approved and the course was resubmitted for review.",
            metadata={"course_appeal_id": appeal.id, "course_id": course.id},
        )
        activity_service.log_activity(
            user=actor,
            category=UserActivityCategoryEnums.APPROVAL,
            action=UserActivityActionEnums.APPEAL_APPROVED,
            summary=f"You approved the appeal for '{course.title}'.",
            target=course,
        )

    return appeal


def reject_appeal(
    *, appeal: CourseAppeal, actor: User, notes: str = ""
) -> CourseAppeal:
    """Reject a Pending appeal. Decision is final per the PRD - the course
    is left untouched (still Draft)."""

    require_role(actor, IsAdminOrSuperAdminRole.allowed_roles)
    if appeal.status != AppealStatus.PENDING:
        raise exceptions.ValidationError(
            f"Appeal cannot be rejected from status '{appeal.status}'."
        )

    appeal.status = AppealStatus.REJECTED
    appeal.decision_notes = notes
    appeal.reviewed_by = actor
    appeal.reviewed_at = timezone.now()
    appeal.save(
        update_fields=[
            "status",
            "decision_notes",
            "reviewed_by",
            "reviewed_at",
            "updated_datetime",
        ]
    )

    Notification.emit_in_app_notification(
        receivers=[appeal.submitted_by],
        title="Appeal rejected",
        content=f"Your appeal for '{appeal.course.title}' was denied. This decision is final.",
        metadata={"course_appeal_id": appeal.id, "course_id": appeal.course_id},
    )
    activity_service.log_activity(
        user=actor,
        category=UserActivityCategoryEnums.APPROVAL,
        action=UserActivityActionEnums.APPEAL_REJECTED,
        summary=f"You rejected the appeal for '{appeal.course.title}'.",
        target=appeal.course,
    )

    return appeal
