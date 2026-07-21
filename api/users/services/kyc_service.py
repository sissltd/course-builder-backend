from rest_framework import exceptions

from api.users.enums import KYCStatus
from api.users.models import KYCVerification, User


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
    """Create a new PENDING KYC submission for `user`.

    Raises ValidationError if a PENDING submission is already awaiting review
    - a user should not be able to queue multiple concurrent submissions
    (they can resubmit once the current one is APPROVED or REJECTED).
    """

    latest = get_latest_verification(user=user)
    if latest is not None and latest.status == KYCStatus.PENDING:
        raise exceptions.ValidationError("A KYC submission is already pending review.")

    return KYCVerification.objects.create(
        user=user,
        country_of_issue=country_of_issue,
        document_type=document_type,
        id_number=id_number,
    )


def require_verified(*, user: User) -> None:
    """Raise ValidationError if `user` has not completed KYC verification."""

    if not is_verified(user=user):
        raise exceptions.ValidationError(
            "Complete KYC verification before withdrawing funds."
        )
