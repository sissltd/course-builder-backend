from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.services.activity_service import log_activity
from api.mie.enums import DeveloperAccountStatus, MiePlanType, MieSourceType
from api.mie.models import DeveloperAccount
from api.mie.services.key_service import issue_credentials, revoke_key
from api.mie.services.webhook_dispatcher import drop_events_for_rejected_account
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums, UserRole
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


def provision_system_account(
    *, actor, email: str, webhook_url: str
) -> tuple[DeveloperAccount, str | None]:
    """Create and approve a platform-owned SYSTEM account (the crawler).

    The only path that ever sets source_type=SYSTEM. The account goes
    through the ordinary lifecycle - created PENDING, then approved by
    approve_developer - so the key/status DB constraints hold exactly as
    for any developer. plan_type is BYPASS_ACCOUNT because the platform
    never pays itself for ideas.

    Idempotent: an email already held by a SYSTEM account is returned
    unchanged, whatever its state, with no key - a suspended crawler is
    restored by approving it, not by re-provisioning. An email held by an
    EXTERNAL developer is refused rather than converted, which would
    silently put a third party under crawler guardrails and on a
    no-payout plan.

    Returns (account, raw_key); raw_key is None when nothing was issued.
    """

    require_role(actor, (UserRole.SUPER_ADMIN,))

    existing = DeveloperAccount.objects.filter(email__iexact=email).first()
    if existing is not None:
        if existing.source_type != MieSourceType.SYSTEM:
            raise exceptions.ValidationError(
                {"email": ["This email belongs to an external developer account."]}
            )
        return existing, None

    with transaction.atomic():
        account = DeveloperAccount.objects.create(
            email=email.lower(),
            webhook_url=webhook_url,
            plan_type=MiePlanType.BYPASS_ACCOUNT,
            source_type=MieSourceType.SYSTEM,
        )
        raw_key = approve_developer(actor=actor, account=account)
        log_activity(
            user=actor,
            category=UserActivityCategoryEnums.CONFIGURATION,
            action=UserActivityActionEnums.ACCOUNT_CREATED,
            summary="Provisioned an MIE system developer account.",
            details={
                "developer_account_id": str(account.id),
                "developer_email": account.email,
            },
            target=account,
        )
    return account, raw_key
