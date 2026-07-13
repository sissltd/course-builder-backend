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


def issue_token(*, user: User, purpose: str) -> tuple[EmailVerificationToken, str]:
    """Invalidate any prior unused token for user+purpose, then issue a new one.

    Returns (token_instance, raw_token). raw_token is never persisted - only
    returned here so the caller can build a link and email it immediately.
    """

    EmailVerificationToken.objects.filter(user=user, purpose=purpose, is_used=False).update(
        is_used=True, used_at=timezone.now()
    )

    raw_token = secrets.token_urlsafe(settings.EMAIL_TOKEN_BYTES)
    record = EmailVerificationToken.objects.create(
        user=user,
        purpose=purpose,
        token_hash=_hash_token(raw_token),
        expires_at=timezone.now() + timedelta(minutes=settings.EMAIL_TOKEN_EXPIRY_MINUTES),
    )
    return record, raw_token


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
        raise exceptions.NotFound("Invalid or expired verification link. Please request a new one.")

    if record.attempts >= settings.EMAIL_TOKEN_MAX_ATTEMPTS:
        raise exceptions.ValidationError("Too many attempts. Please request a new link.")

    if record.expires_at < timezone.now():
        raise exceptions.ValidationError("This link has expired. Please request a new one.")

    record.is_used = True
    record.used_at = timezone.now()
    record.save(update_fields=["is_used", "used_at", "updated_datetime"])
    return record


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
