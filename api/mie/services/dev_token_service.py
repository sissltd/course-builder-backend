from datetime import datetime, timedelta, timezone as dt_timezone
from uuid import uuid4

import jwt as pyjwt
from django.conf import settings

from api.mie.enums import DeveloperAccountStatus
from api.mie.models import DeveloperAccount


class DevTokenInvalid(Exception):
    """Raised when a platform dev-session token cannot be trusted."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _signing_key() -> str:
    return settings.SIMPLE_JWT["SIGNING_KEY"]


def _access_lifetime() -> timedelta:
    return settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"]


def issue_dev_token(account: DeveloperAccount) -> str:
    """Mint a short-lived session token for platform (OTP) logins.

    Scoped to the MIE surface only - it carries no Django user id and is
    worthless outside mie endpoints - so external developers never need
    rows in the platform's user table. Lifetime mirrors SIMPLE_JWT access
    tokens so the FE treats both session kinds identically.
    """

    now = datetime.now(dt_timezone.utc)
    payload = {
        "mie_developer_id": str(account.id),
        "typ": "mie_dev",
        "iat": now,
        "exp": now + _access_lifetime(),
        "jti": uuid4().hex,
    }
    return pyjwt.encode(payload, _signing_key(), algorithm="HS256")


def resolve_dev_token(token: str) -> DeveloperAccount:
    """Validate a platform token and return its approved DeveloperAccount.

    Status is re-checked on every request so a suspension kills live
    sessions immediately rather than at next login.
    """

    try:
        payload = pyjwt.decode(
            token,
            _signing_key(),
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "mie_developer_id", "typ"]},
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise DevTokenInvalid("token_expired", "Session expired; sign in again.") from exc
    except pyjwt.InvalidTokenError as exc:
        raise DevTokenInvalid("invalid_token", "Invalid session token.") from exc

    if payload.get("typ") != "mie_dev":
        raise DevTokenInvalid("invalid_token", "Invalid session token.")

    account = DeveloperAccount.objects.filter(id=payload["mie_developer_id"]).first()
    if account is None:
        raise DevTokenInvalid("account_not_found", "Developer account no longer exists.")
    if account.status != DeveloperAccountStatus.APPROVED:
        raise DevTokenInvalid(
            "account_not_active",
            "This developer account does not have an active session.",
        )
    return account
