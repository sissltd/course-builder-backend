from django.utils import timezone
from rest_framework import exceptions

from api.mie.enums import DeveloperAccountStatus
from api.mie.models import DeveloperAccount
from api.mie.services.key_service import issue_credentials, revoke_key
from api.mie.services.webhook_dispatcher import drop_events_for_rejected_account
from api.users.enums import UserRole
from api.users.permissions import require_role


def register_developer(*, email: str, webhook_url: str, plan_type: str) -> DeveloperAccount:
    """Create a PENDING account from the minimal registration payload.

    The email is the identity for both API-key issuance and platform OTP
    sign-in, so it is unique across accounts regardless of status.
    """

    if DeveloperAccount.objects.filter(email__iexact=email).exists():
        raise exceptions.ValidationError(
            {"email": ["A developer account with this email already exists."]}
        )
    return DeveloperAccount.objects.create(
        email=email.lower(), webhook_url=webhook_url, plan_type=plan_type
    )


def approve_developer(*, actor, account: DeveloperAccount) -> str | None:
    """Approve the account; returns the raw API key when freshly issued.

    Approval is reversible - REJECTED and SUSPENDED accounts can be
    approved again. Credentials are issued only when none exist (first
    approval, or re-approval after rejection wiped them); the raw key is
    returned exactly once to be shown to the developer, or None when the
    existing key stays valid.

    Status flips before credential issuance - the DB constraint only
    permits key material on an already-active row, so an interrupted
    approval can never leave credentials stranded on a PENDING account.
    """

    require_role(actor, (UserRole.SUPER_ADMIN,))
    if account.status == DeveloperAccountStatus.APPROVED:
        raise exceptions.ValidationError({"status": ["Account is already approved."]})

    account.status = DeveloperAccountStatus.APPROVED
    account.approved_by = actor
    account.decided_at = timezone.now()
    account.save(update_fields=["status", "approved_by", "decided_at", "updated_datetime"])

    if not account.api_key_hash:
        return issue_credentials(account)
    return None


def reject_developer(*, actor, account: DeveloperAccount) -> None:
    """Reject the account from any state; revokes key material.

    Reachable from any state so decisions stay reversible. Revocation is
    mandatory on the way out - the DB constraint forbids credentials on a
    non-active row, and it also freezes access immediately. Re-approval
    issues fresh credentials.
    """

    require_role(actor, (UserRole.SUPER_ADMIN,))
    if account.status == DeveloperAccountStatus.REJECTED:
        raise exceptions.ValidationError({"status": ["Account is already rejected."]})

    revoke_key(account)
    account.status = DeveloperAccountStatus.REJECTED
    account.decided_at = timezone.now()
    account.save(update_fields=["status", "decided_at", "updated_datetime"])

    # A rejected account can never receive again; fail its pending events
    # now so they stop accumulating behind it.
    drop_events_for_rejected_account(account)


def suspend_developer(*, actor, account: DeveloperAccount) -> None:
    """Freeze an approved account without touching its queue history."""

    require_role(actor, (UserRole.SUPER_ADMIN,))
    if account.status != DeveloperAccountStatus.APPROVED:
        raise exceptions.ValidationError(
            {"status": ["Only approved accounts can be suspended."]}
        )

    account.status = DeveloperAccountStatus.SUSPENDED
    account.decided_at = timezone.now()
    account.save(update_fields=["status", "decided_at", "updated_datetime"])
