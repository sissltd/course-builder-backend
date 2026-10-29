"""Deleting an account: deactivate it for good and scrub its personal data.

The row itself stays. Courses, payouts, review decisions and audit logs all
point at users, and a hard delete would cascade through them (including
wallet ledger rows). So "delete" here means: the person can never sign in
again, everything that identifies them is blanked, and every record of what
they did survives against an anonymous account.

Irreversible - reinstating or reactivating an erased account is refused.
"""

import logging
import uuid

from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions, status
from rest_framework.exceptions import APIException

from api.authentication.models import (
    EmailVerificationToken,
    ExternalIdentity,
    MFAChallenge,
    MFADevice,
    MFARecoveryCode,
)
from api.authentication.services import activity_service
from api.authorization import codenames
from api.authorization.services import permission_service
from api.collaborators.models import WorkspaceCollaborator
from api.onboarding.models import CreatorProfile
from api.payments.models import BankAccount
from api.users.enums import (
    AccountStatus,
    UserActivityActionEnums,
    UserActivityCategoryEnums,
    UserRole,
)
from api.users.models import KYCVerification, User
from api.users.services.account_admin_service import STAFF, TEAMS
from api.wallet.enums import WithdrawalRequestStatus
from api.wallet.models import Wallet, WithdrawalRequest
from core.models import TransferOutboxEvent

logger = logging.getLogger(__name__)

ERASE_PERMISSION = {
    STAFF: codenames.STAFF_DELETE,
    TEAMS: codenames.TEAMS_DELETE_ACCOUNT,
}

#: Identifying fields on User, reset to their model defaults.
USER_PII_FIELDS = (
    "first_name",
    "last_name",
    "country",
    "state",
    "address",
    "phone_number",
    "date_of_birth",
    "sex",
    "timezone",
    "avatar_url",
    "kyc_first_name",
    "kyc_last_name",
    "kyc_date_of_birth",
    "kyc_gender",
    "kyc_document_image",
    "kyc_phone",
    "kyc_address",
    "liveness_selfie",
    "liveness_score",
    "liveness_passes",
)

#: Identifying fields on KYC submissions. `date_of_birth` is required on that
#: model and is kept; see the handover doc's open questions.
KYC_PII_FIELDS = ("id_number", "kyc_entity_id", "kyc_failure_message")

#: Payout states where money is still moving through a provider.
IN_FLIGHT_TRANSFER_STATUSES = (
    TransferOutboxEvent.Status.PENDING,
    TransferOutboxEvent.Status.PROCESSING,
    TransferOutboxEvent.Status.SUBMITTED,
)

ERASED_EMAIL_DOMAIN = "erased.invalid"


class AccountHasMoney(APIException):
    """The account still holds a balance or has a payout in flight."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "account_has_money"
    default_detail = "This account still has money on the platform."


def erase_account(
    *,
    actor: User,
    user: User,
    audience: str,
    reason: str,
    confirm_email: str,
    request=None,
) -> User:
    permission_service.require_permission(actor, ERASE_PERMISSION[audience])
    if user.id == actor.id:
        raise exceptions.ValidationError("You cannot delete your own account.")
    if user.is_superuser or user.role == UserRole.SUPER_ADMIN:
        raise exceptions.PermissionDenied("The Super Admin account cannot be deleted.")
    if user.erased_at is not None:
        raise exceptions.ValidationError("This account has already been deleted.")
    if confirm_email.strip().lower() != user.email.lower():
        raise exceptions.ValidationError(
            {"confirm_email": "Type the account's email address to confirm deletion."}
        )
    if Wallet.objects.filter(user=user, balance__gt=0).exists():
        raise AccountHasMoney(
            "This account still has a wallet balance. Pay it out or adjust it to "
            "zero before deleting the account."
        )
    if TransferOutboxEvent.objects.filter(
        user=user, status__in=IN_FLIGHT_TRANSFER_STATUSES
    ).exists():
        raise AccountHasMoney(
            "A payout for this account is still being processed. Wait for it to "
            "settle before deleting the account."
        )

    from api.authorization.services.role_admin_service import revoke_sessions_bulk
    from api.reviews.services import review_service

    stored_files = [
        value for value in (user.kyc_document_image, user.liveness_selfie) if value
    ]
    now = timezone.now()
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        for field in USER_PII_FIELDS:
            setattr(user, field, User._meta.get_field(field).get_default())
        user.email = f"erased+{uuid.uuid4().hex}@{ERASED_EMAIL_DOMAIN}"
        user.first_name, user.last_name = "Deleted", "User"
        user.set_unusable_password()
        user.is_active = False
        user.status = AccountStatus.DEACTIVATED
        user.erased_at = now
        user.erased_by = actor
        user.save()

        KYCVerification.objects.filter(user=user).update(
            **{
                field: KYCVerification._meta.get_field(field).get_default()
                for field in KYC_PII_FIELDS
            },
            updated_datetime=now,
        )
        CreatorProfile.objects.filter(user=user).update(primary_expertise_other="")
        for model in (
            ExternalIdentity,
            MFADevice,
            MFARecoveryCode,
            MFAChallenge,
            EmailVerificationToken,
        ):
            model.objects.filter(user=user).delete()
        revoke_sessions_bulk(user_ids=[user.id])
        BankAccount.objects.filter(user=user, is_deleted=False).update(
            is_deleted=True,
            deleted_datetime=now,
            account_name="Deleted User",
            account_number="",
            is_default=False,
            updated_datetime=now,
        )
        WithdrawalRequest.objects.filter(
            user=user, status=WithdrawalRequestStatus.PENDING_CONFIRMATION
        ).update(status=WithdrawalRequestStatus.EXPIRED, updated_datetime=now)
        WorkspaceCollaborator.objects.filter(user=user).update(
            status=WorkspaceCollaborator.Status.REMOVED, updated_datetime=now
        )
        seats_released = review_service.release_open_seats(user=user)
        activity_service.log_activity(
            user=actor,
            category=UserActivityCategoryEnums.PRIVACY,
            action=UserActivityActionEnums.ACCOUNT_ERASED,
            summary="Deleted an account and erased its personal data.",
            details={
                "target_user_id": str(user.id),
                "audience": audience,
                "reason": reason,
                "seats_released": seats_released,
            },
            request=request,
        )
        transaction.on_commit(lambda: _delete_stored_files(stored_files))
    return user


def _delete_stored_files(file_keys: list) -> None:
    """Remove KYC images from storage after commit. Failures are logged, not raised:
    the account is already erased and its references are gone."""

    from shared.services.storage_service import StorageService

    for file_key in file_keys:
        try:
            StorageService.delete_file(file_key)
        except Exception:  # noqa: BLE001 - see docstring
            logger.exception("could not delete an erased account's stored file")
