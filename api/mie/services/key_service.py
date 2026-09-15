import hashlib
import secrets

from django.utils import timezone

from api.mie.enums import DeveloperAccountStatus
from api.mie.models import DeveloperAccount

API_KEY_PREFIX = "scb_live_"
API_KEY_RANDOM_BYTES = 32
KEY_DISPLAY_PREFIX_LENGTH = 16


class ApiKeyRejected(Exception):
    """Raised when a presented API key cannot be trusted."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def generate_raw_key() -> str:
    """Build a full key: scb_live_ + ~256 bits of url-safe entropy."""

    return API_KEY_PREFIX + secrets.token_urlsafe(API_KEY_RANDOM_BYTES)


def hash_raw_key(raw_key: str) -> str:
    """SHA-256 hex digest of the full key.

    A fast digest is appropriate here because keys carry ~256 bits of
    entropy - there is nothing to brute-force - and it keeps verification
    to one indexed DB lookup instead of a per-row expensive comparison.
    """

    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_signing_secret() -> str:
    return secrets.token_urlsafe(API_KEY_RANDOM_BYTES)


def issue_credentials(account: DeveloperAccount) -> str:
    """Generate and persist a fresh key pair of credentials for `account`.

    Returns the raw key, which exists exactly once - here - before being
    shown to the developer at approval/rotation; only the hash survives.
    Also (re)issues the webhook signing secret, which unlike the key is
    retrievable via /me.
    """

    raw_key = generate_raw_key()
    account.api_key_prefix = raw_key[:KEY_DISPLAY_PREFIX_LENGTH]
    account.api_key_hash = hash_raw_key(raw_key)
    account.api_key_issued_at = timezone.now()
    account.signing_secret = generate_signing_secret()
    account.save(
        update_fields=[
            "api_key_prefix",
            "api_key_hash",
            "api_key_issued_at",
            "signing_secret",
            "updated_datetime",
        ]
    )
    return raw_key


def revoke_key(account: DeveloperAccount) -> None:
    """Immediately invalidate all credential material.

    Clears both the API key and the webhook signing secret - rejection
    must leave zero active credentials on the row (a DB constraint
    enforces exactly this), and a fresh secret is minted on re-approval.
    """

    account.api_key_hash = ""
    account.api_key_prefix = ""
    account.signing_secret = ""
    account.save(
        update_fields=["api_key_prefix", "api_key_hash", "signing_secret", "updated_datetime"]
    )


def authenticate_key(raw_key: str) -> DeveloperAccount:
    """Resolve a presented key to an active DeveloperAccount.

    The key is hashed once and the digest is matched against the indexed
    api_key_hash column, so verification costs exactly one indexed lookup
    and there is no candidate set to disambiguate. Matching on the digest
    rather than the display prefix is what makes that true: the prefix is
    only 7 random characters wide once "scb_live_" is removed, so two
    accounts can legitimately share one.

    A revoked account fails closed for free - revoke_key blanks the hash,
    and "" can never equal a 64-character digest.

    Raises ApiKeyRejected with a stable machine code for every failure
    mode.
    """

    # Cheap shape check first: a key for some other service never reaches
    # the database.
    if not raw_key or not raw_key.startswith(API_KEY_PREFIX):
        raise ApiKeyRejected("invalid_api_key", "Invalid API key.")

    candidate = DeveloperAccount.objects.filter(
        api_key_hash=hash_raw_key(raw_key)
    ).first()
    if candidate is None:
        raise ApiKeyRejected("invalid_api_key", "Invalid API key.")

    _enforce_active_status(candidate)

    DeveloperAccount.objects.filter(id=candidate.id).update(
        api_key_last_used_at=timezone.now()
    )
    return candidate


def _enforce_active_status(account: DeveloperAccount) -> None:
    if account.status == DeveloperAccountStatus.SUSPENDED:
        raise ApiKeyRejected(
            "account_suspended",
            "This developer account is suspended; its credentials are frozen.",
        )
    if account.status != DeveloperAccountStatus.APPROVED:
        raise ApiKeyRejected(
            "account_not_active",
            "This developer account has no active credentials.",
        )
