from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.services import activity_service
from api.notification.models import Notification
from api.users.enums import (
    KYCStatus,
    UserActivityActionEnums,
    UserActivityCategoryEnums,
)
from api.users.models import KYCVerification, User
from api.users.permissions import IsAdminOrSuperAdminRole

REVIEWABLE_STATUSES = (KYCStatus.PENDING,)


def get_latest_verification(*, user: User) -> KYCVerification | None:
    """Most recent KYC submission for `user`, if any (newest first by Meta.ordering)."""

    return KYCVerification.objects.filter(user=user).first()


def is_verified(*, user: User) -> bool:
    """True if `user`'s latest KYC submission was approved.

    Used by wallet_service to gate withdrawal requests - a rejected or
    still-pending submission (or no submission at all) means not verified.
    """

    latest = get_latest_verification(user=user)
    return latest is not None and latest.status == KYCStatus.APPROVED


def submit_verification(
    *, user: User, country_of_issue: str, document_type: str, id_number: str
) -> KYCVerification:
    """Create a new PENDING KYC submission for `user`, and alert Admins/Super
    Admins that a request is waiting for them.

    Raises ValidationError if a PENDING submission is already awaiting review
    - a user should not be able to queue multiple concurrent submissions
    (they can resubmit once the current one is APPROVED or REJECTED).
    """

    latest = get_latest_verification(user=user)
    if latest is not None and latest.status == KYCStatus.PENDING:
        raise exceptions.ValidationError("A KYC submission is already pending review.")

    verification = KYCVerification.objects.create(
        user=user,
        country_of_issue=country_of_issue,
        document_type=document_type,
        id_number=id_number,
    )
    _notify_admins_of_new_submission(verification)
    return verification


def _notify_admins_of_new_submission(verification: KYCVerification) -> None:
    admins = User.objects.filter(role__in=IsAdminOrSuperAdminRole.allowed_roles)
    if not admins:
        return

    Notification.emit_in_app_notification(
        receivers=list(admins),
        title="New KYC verification request",
        content=f"{verification.user.email} submitted a KYC verification request.",
        metadata={
            "kyc_verification_id": verification.id,
            "user_id": verification.user_id,
        },
    )


def approve_verification(
    *, verification: KYCVerification, reviewer: User
) -> KYCVerification:
    """Approve a PENDING KYC submission.

    Raises ValidationError if the submission isn't awaiting review (prevents
    double-approval). Notifies the submitting user and logs the reviewer's
    activity, mirroring review_service.approve_course.
    """

    if verification.status not in REVIEWABLE_STATUSES:
        raise exceptions.ValidationError(
            f"Submission cannot be approved from status '{verification.status}'."
        )

    with transaction.atomic():
        verification.status = KYCStatus.APPROVED
        verification.reviewed_by = reviewer
        verification.reviewed_at = timezone.now()
        verification.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "updated_datetime"]
        )

        Notification.emit_in_app_notification(
            receivers=[verification.user],
            title="KYC verification approved",
            content="Your identity verification has been approved.",
            metadata={"kyc_verification_id": verification.id},
        )
        activity_service.log_activity(
            user=reviewer,
            category=UserActivityCategoryEnums.KYC,
            action=UserActivityActionEnums.KYC_APPROVED,
            summary=f"You approved a KYC submission from {verification.user.email}.",
            target=verification,
        )

    return verification


def reject_verification(
    *, verification: KYCVerification, reviewer: User, rejection_reason: str
) -> KYCVerification:
    """Reject a PENDING KYC submission with a reason the user can act on.

    Raises ValidationError if the submission isn't awaiting review, or if
    rejection_reason is empty. Notifies the submitting user (who may then
    resubmit - see submit_verification) and logs the reviewer's activity.
    """

    if not rejection_reason:
        raise exceptions.ValidationError(
            {"rejection_reason": "A rejection reason is required."}
        )
    if verification.status not in REVIEWABLE_STATUSES:
        raise exceptions.ValidationError(
            f"Submission cannot be rejected from status '{verification.status}'."
        )

    with transaction.atomic():
        verification.status = KYCStatus.REJECTED
        verification.rejection_reason = rejection_reason
        verification.reviewed_by = reviewer
        verification.reviewed_at = timezone.now()
        verification.save(
            update_fields=[
                "status",
                "rejection_reason",
                "reviewed_by",
                "reviewed_at",
                "updated_datetime",
            ]
        )

        Notification.emit_in_app_notification(
            receivers=[verification.user],
            title="KYC verification rejected",
            content=f"Your identity verification was rejected: {rejection_reason}",
            metadata={"kyc_verification_id": verification.id},
        )
        activity_service.log_activity(
            user=reviewer,
            category=UserActivityCategoryEnums.KYC,
            action=UserActivityActionEnums.KYC_REJECTED,
            summary=f"You rejected a KYC submission from {verification.user.email}.",
            target=verification,
        )

    return verification


def require_verified(*, user: User) -> None:
    """Raise ValidationError if `user` has not completed KYC verification."""

    if not is_verified(user=user):
        raise exceptions.ValidationError(
            "Complete KYC verification before withdrawing funds."
        )
