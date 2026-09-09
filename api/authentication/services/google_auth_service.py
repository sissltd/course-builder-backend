from dataclasses import dataclass

import cachecontrol
import logging
import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from rest_framework import exceptions

from api.authentication.enums import ExternalIdentityProvider, TokenPurpose
from api.authentication.models import ExternalIdentity
from api.authentication.services import (
    activity_service,
    authentication_service,
    token_service,
)
from api.users.enums import AccountStatus, UserActivityActionEnums, UserRole
from api.users.models import User

INVALID_GOOGLE_CREDENTIAL = "Invalid Google credential."
PUBLIC_GOOGLE_ROLES = (UserRole.COURSE_CREATOR, UserRole.CREATOR_REVIEWER)
_GOOGLE_HTTP_REQUEST = google_requests.Request(
    session=cachecontrol.CacheControl(requests.Session())
)

logger = logging.getLogger(__name__)


class GoogleAuthenticationUnavailable(exceptions.APIException):
    """Google authentication cannot run because the deployment is incomplete."""

    status_code = 503
    default_detail = "Google authentication is temporarily unavailable."
    default_code = "google_auth_unavailable"


@dataclass(frozen=True)
class GoogleIdentityClaims:
    """Verified identity attributes consumed by the account service."""

    subject: str
    email: str


def verify_google_id_token(*, raw_token: str) -> GoogleIdentityClaims:
    """Verify a Google ID token and return only trusted identity claims."""

    client_ids = tuple(settings.GOOGLE_OAUTH_CLIENT_IDS)
    if not client_ids:
        raise GoogleAuthenticationUnavailable()

    try:
        claims = google_id_token.verify_oauth2_token(
            raw_token,
            _GOOGLE_HTTP_REQUEST,
            audience=list(client_ids),
        )
    except (ValueError, google_exceptions.GoogleAuthError) as exc:
        # The client only ever sees the generic message; the reason (expired,
        # wrong audience, bad signature, ...) goes to server logs for debugging.
        logger.warning("Google ID token verification failed: %s", exc)
        raise exceptions.ValidationError(
            {"id_token": INVALID_GOOGLE_CREDENTIAL}
        ) from exc

    if claims.get("aud") not in client_ids:
        logger.warning(
            "Google ID token audience %s is not in GOOGLE_OAUTH_CLIENT_IDS.",
            claims.get("aud"),
        )
        raise exceptions.ValidationError({"id_token": INVALID_GOOGLE_CREDENTIAL})

    subject = claims.get("sub")
    email = claims.get("email")
    if (
        not isinstance(subject, str)
        or not subject
        or not isinstance(email, str)
        or not email
        or claims.get("email_verified") is not True
    ):
        raise exceptions.ValidationError({"id_token": INVALID_GOOGLE_CREDENTIAL})

    return GoogleIdentityClaims(subject=subject, email=email.strip().lower())


def signup_with_google(
    *,
    raw_token: str,
    first_name: str,
    last_name: str,
    country: str,
    terms_accepted: bool,
    role: str,
    request=None,
) -> tuple[dict, bool]:
    """Create or link a public account, then issue the normal local session."""

    claims = verify_google_id_token(raw_token=raw_token)
    try:
        with transaction.atomic():
            user, created = _resolve_user(
                claims=claims,
                allow_create=True,
                role=role,
                first_name=first_name,
                last_name=last_name,
                country=country,
                terms_accepted=terms_accepted,
                request=request,
            )
            _prepare_for_google_login(user=user)
    except IntegrityError as exc:
        raise exceptions.ValidationError(
            {"id_token": INVALID_GOOGLE_CREDENTIAL}
        ) from exc

    return (
        authentication_service.finish_login(
            user=user, request=request, mfa_verified=True
        ),
        created,
    )


def login_with_google(*, raw_token: str, request=None) -> dict:
    """Authenticate an existing public account through its Google identity."""

    claims = verify_google_id_token(raw_token=raw_token)
    try:
        with transaction.atomic():
            user, _created = _resolve_user(
                claims=claims,
                allow_create=False,
                request=request,
            )
            _prepare_for_google_login(user=user)
    except IntegrityError as exc:
        raise exceptions.ValidationError(
            {"id_token": INVALID_GOOGLE_CREDENTIAL}
        ) from exc

    return authentication_service.finish_login(
        user=user, request=request, mfa_verified=True
    )


def _resolve_user(
    *,
    claims: GoogleIdentityClaims,
    allow_create: bool,
    request=None,
    role: str | None = None,
    first_name: str = "",
    last_name: str = "",
    country: str = "",
    terms_accepted: bool = False,
) -> tuple[User, bool]:
    identity = (
        ExternalIdentity.objects.select_for_update()
        .filter(
            provider=ExternalIdentityProvider.GOOGLE,
            subject=claims.subject,
        )
        .first()
    )
    if identity is not None:
        user = User.objects.select_for_update().get(pk=identity.user_id)
        _ensure_google_account_is_eligible(user=user)
        _ensure_google_role(user=user)
        _record_terms_acceptance(
            user=user,
            requested=allow_create and terms_accepted,
        )
        return user, False

    user = User.objects.select_for_update().filter(email__iexact=claims.email).first()
    created = user is None
    if created:
        if not allow_create:
            raise exceptions.ValidationError(
                {
                    "id_token": (
                        "No account is linked to this Google identity. "
                        "Please sign up first."
                    )
                }
            )
        user = User.objects.create_user(
            email=claims.email,
            password=None,
            first_name=first_name,
            last_name=last_name,
            country=country.upper(),
            terms_accepted_at=timezone.now() if terms_accepted else None,
            role=role,
            is_active=True,
            status=AccountStatus.ACTIVE,
        )
        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.ACCOUNT_CREATED,
            summary=f"Account created with role {role} through Google.",
            request=request,
        )
        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.ACCOUNT_VERIFIED,
            summary="Email verified by Google during signup.",
            request=request,
        )
    else:
        _ensure_google_account_is_eligible(user=user)
        _ensure_google_role(user=user)
        _record_terms_acceptance(
            user=user,
            requested=allow_create and terms_accepted,
        )

    existing_provider_identity = ExternalIdentity.objects.filter(
        user=user,
        provider=ExternalIdentityProvider.GOOGLE,
    ).first()
    if existing_provider_identity is not None:
        raise exceptions.ValidationError({"id_token": INVALID_GOOGLE_CREDENTIAL})

    ExternalIdentity.objects.create(
        user=user,
        provider=ExternalIdentityProvider.GOOGLE,
        subject=claims.subject,
        email=claims.email,
    )
    activity_service.log_auth_activity(
        user=user,
        action=UserActivityActionEnums.GOOGLE_IDENTITY_LINKED,
        summary="Google identity linked to account.",
        request=request,
    )

    if not created and (
        not user.is_active or user.status == AccountStatus.PENDING_VERIFICATION
    ):
        user.is_active = True
        user.status = AccountStatus.ACTIVE
        user.save(update_fields=["is_active", "status", "updated_datetime"])
        token_service.invalidate_tokens(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION
        )
        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.ACCOUNT_VERIFIED,
            summary="Email verified by Google while linking account.",
            request=request,
        )

    return user, created


def _ensure_google_role(*, user: User) -> None:
    if user.role not in PUBLIC_GOOGLE_ROLES:
        raise exceptions.ValidationError(
            {"id_token": "Google authentication is unavailable for this account."}
        )


def _ensure_google_account_is_eligible(*, user: User) -> None:
    """Refuse blocked accounts before Google auth mutates local state."""

    if user.status in (AccountStatus.SUSPENDED, AccountStatus.DEACTIVATED) or (
        not user.is_active and user.status != AccountStatus.PENDING_VERIFICATION
    ):
        raise exceptions.ValidationError(
            {"id_token": "This account is not active. Please contact support."}
        )


def _record_terms_acceptance(*, user: User, requested: bool) -> None:
    if requested and user.terms_accepted_at is None:
        user.terms_accepted_at = timezone.now()
        user.save(update_fields=["terms_accepted_at", "updated_datetime"])


def _prepare_for_google_login(*, user: User) -> None:
    _ensure_google_account_is_eligible(user=user)

    now = timezone.now()
    if user.locked_until and user.locked_until > now:
        minutes_left = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        raise exceptions.ValidationError(
            {
                "id_token": (
                    "Too many failed attempts. "
                    f"Try again in {minutes_left} minute(s)."
                )
            }
        )

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login = now
    user.save(
        update_fields=[
            "failed_login_attempts",
            "locked_until",
            "last_login",
            "updated_datetime",
        ]
    )
