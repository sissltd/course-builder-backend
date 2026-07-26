import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.models import EmailVerificationToken
from api.users.models import User


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(
    *, user: User, purpose: str, extra_fields: dict | None = None
) -> tuple[EmailVerificationToken, str]:
    """Invalidate any prior unused token for user+purpose, then issue a new one.

    Returns (token_instance, raw_token). raw_token is never persisted - only
    returned here so the caller can build a link and email it immediately.
    `extra_fields` sets additional columns on the row (e.g. `new_email` for
    an EMAIL_CHANGE-purpose token) without every other purpose needing to
    know about them.
    """

    EmailVerificationToken.objects.filter(
        user=user, purpose=purpose, is_used=False
    ).update(is_used=True, used_at=timezone.now())

    raw_token = secrets.token_urlsafe(settings.EMAIL_TOKEN_BYTES)
    record = EmailVerificationToken.objects.create(
        user=user,
        purpose=purpose,
        token_hash=_hash_token(raw_token),
        expires_at=timezone.now()
        + timedelta(minutes=settings.EMAIL_TOKEN_EXPIRY_MINUTES),
        **(extra_fields or {}),
    )
    return record, raw_token


def issue_numeric_code(
    *, user: User, purpose: str, length: int, expiry_minutes: int
) -> tuple[EmailVerificationToken, str]:
    """Same one-active-token-per-purpose contract as issue_token, but the raw
    value is a short numeric code (for OTP-style manual entry, e.g. confirming
    a withdrawal) rather than a URL-safe link token. Stored identically -
    verify_token and can_resend work unchanged for either kind of value.
    """

    EmailVerificationToken.objects.filter(
        user=user, purpose=purpose, is_used=False
    ).update(is_used=True, used_at=timezone.now())

    raw_code = "".join(secrets.choice("0123456789") for _ in range(length))
    record = EmailVerificationToken.objects.create(
        user=user,
        purpose=purpose,
        token_hash=_hash_token(raw_code),
        expires_at=timezone.now() + timedelta(minutes=expiry_minutes),
    )
    return record, raw_code


def verify_token(*, user: User, purpose: str, token: str) -> EmailVerificationToken:
    """Verify `token` against the active token for user+purpose.

    Looks up by hash directly (O(1) indexed lookup on the unique token_hash),
    then cross-checks user+purpose as defense in depth. Raises NotFound if no
    matching unused token exists (wrong token, wrong user, or wrong purpose -
    all reported identically so a mismatch never reveals which part was
    wrong). Raises ValidationError if expired or the abuse-attempt guard trips.
    """

    record = EmailVerificationToken.objects.filter(
        token_hash=_hash_token(token), is_used=False
    ).first()
    if not record or record.user_id != user.id or record.purpose != purpose:
        raise exceptions.NotFound(
            "Invalid or expired verification code. Please request a new one."
        )

    if record.attempts >= settings.EMAIL_TOKEN_MAX_ATTEMPTS:
        raise exceptions.ValidationError(
            "Too many attempts. Please request a new code."
        )

    if record.expires_at < timezone.now():
        raise exceptions.ValidationError(
            "This code has expired. Please request a new one."
        )

    record.is_used = True
    record.used_at = timezone.now()
    record.save(update_fields=["is_used", "used_at", "updated_datetime"])
    return record


def verify_token_without_user(*, purpose: str, token: str) -> EmailVerificationToken:
    """Same checks as verify_token, but resolves the user from the token
    itself instead of requiring the caller to already know it.

    For flows where the confirming link is the only piece of identifying
    information available - e.g. email-change confirmation, opened from the
    new inbox where the user may not be authenticated at all.
    """

    record = EmailVerificationToken.objects.filter(
        token_hash=_hash_token(token), is_used=False, purpose=purpose
    ).first()
    if not record:
        raise exceptions.NotFound(
            "Invalid or expired verification link. Please request a new one."
        )

    if record.attempts >= settings.EMAIL_TOKEN_MAX_ATTEMPTS:
        raise exceptions.ValidationError(
            "Too many attempts. Please request a new code."
        )

    if record.expires_at < timezone.now():
        raise exceptions.ValidationError(
            "This link has expired. Please request a new one."
        )

    record.is_used = True
    record.used_at = timezone.now()
    record.save(update_fields=["is_used", "used_at", "updated_datetime"])
    return record


def invalidate_tokens(*, user: User, purpose: str) -> int:
    """Burn every unused token for user+purpose. Returns how many were burned.

    Used when an outstanding link must stop working even though nobody consumed
    it - revoking a staff invitation, for example. Marks tokens used rather than
    deleting them so the audit trail survives.
    """

    return EmailVerificationToken.objects.filter(
        user=user, purpose=purpose, is_used=False
    ).update(is_used=True, used_at=timezone.now())


def can_resend(*, user: User, purpose: str) -> bool:
    """True if there's no active token, or the active one was issued more than
    settings.EMAIL_TOKEN_RESEND_COOLDOWN_SECONDS ago."""

    record = (
        EmailVerificationToken.objects.filter(user=user, purpose=purpose, is_used=False)
        .order_by("-created_datetime")
        .first()
    )
    if not record:
        return True

    cooldown_expires_at = record.created_datetime + timedelta(
        seconds=settings.EMAIL_TOKEN_RESEND_COOLDOWN_SECONDS
    )
    return timezone.now() >= cooldown_expires_at
