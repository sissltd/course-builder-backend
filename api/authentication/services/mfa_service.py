import base64
import hashlib
import io
import secrets
import time
from datetime import timedelta

import pyotp
import qrcode
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.models import MFAChallenge, MFADevice, MFARecoveryCode
from api.authentication.services import activity_service
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums, UserRole
from api.users.models import User
from shared.utils.encryption import decrypt_field, encrypt_field

#: TOTP step size, seconds - standard RFC 6238 default, matches pyotp's own default.
TOTP_STEP_SECONDS = 30
#: How many ±steps of clock drift a code is accepted for. Do not widen this -
#: every extra step is directly added guessing surface.
TOTP_VALID_WINDOW = 1
#: How many recovery codes are generated per enrollment/regeneration.
RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_LENGTH = 10

MFA_CHALLENGE_LIFETIME_MINUTES = 5
#: Tighter than login's 5-attempts/15-min: a 6-digit TOTP space is far
#: smaller than password entropy per guess, so the lockout is tighter too.
MFA_MAX_FAILED_ATTEMPTS = 5
MFA_LOCKOUT_DURATION_MINUTES = 30

#: Roles MFA is mandatory for - enforced at login (challenge required once
#: enrolled) and via grace-period tracking (User.mfa_grace_period_ends_at)
#: until then.
MFA_MANDATED_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN)


def _hash_challenge_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_recovery_code() -> str:
    """A URL-safe, easy-to-transcribe code - not meant to be typed often."""

    return secrets.token_hex(RECOVERY_CODE_LENGTH // 2)


def get_device(*, user: User) -> MFADevice | None:
    return MFADevice.objects.filter(user=user).first()


def is_mfa_enabled(*, user: User) -> bool:
    device = get_device(user=user)
    return device is not None and device.is_enabled


def is_locked_out(*, user: User) -> bool:
    return bool(user.locked_until and user.locked_until > timezone.now())


def _register_failed_mfa_attempt(*, user: User, device: MFADevice) -> None:
    device.failed_attempts += 1
    update_fields = ["failed_attempts", "updated_datetime"]
    if device.failed_attempts >= MFA_MAX_FAILED_ATTEMPTS:
        user.locked_until = timezone.now() + timedelta(
            minutes=MFA_LOCKOUT_DURATION_MINUTES
        )
        user.save(update_fields=["locked_until"])
        device.failed_attempts = 0
    device.save(update_fields=update_fields)


# >>>>>>>>>>>>>>>>>>>> Enrollment <<<<<<<<<<<<<<<<<<<<<<


def enroll(*, user: User) -> dict:
    """Start (or restart) enrollment: generate a fresh secret and overwrite
    any existing not-yet-confirmed device row. Explicit overwrite, not an
    upsert-and-keep-old-if-present - re-scanning a lost QR code must
    invalidate the old pending secret so it can never later be confirmed
    from a stale client-side cache."""

    secret = pyotp.random_base32()
    device, _created = MFADevice.objects.update_or_create(
        user=user,
        defaults={
            "secret_encrypted": encrypt_field(secret),
            "is_enabled": False,
            "enrolled_at": None,
            "last_used_window": None,
            "failed_attempts": 0,
        },
    )
    otpauth_uri = pyotp.TOTP(secret).provisioning_uri(
        name=user.email, issuer_name="SoluDesks"
    )

    qr_buffer = io.BytesIO()
    qrcode.make(otpauth_uri).save(qr_buffer, format="PNG")
    qr_code_base64 = base64.b64encode(qr_buffer.getvalue()).decode()

    return {
        "secret": secret,
        "otpauth_uri": otpauth_uri,
        "qr_code_base64": qr_code_base64,
    }


def confirm_enrollment(*, user: User, code: str, request=None) -> list[str]:
    """Confirm the pending device with a live code; on success, enables the
    device and issues a fresh batch of recovery codes (returned once, as
    plaintext, here only)."""

    device = get_device(user=user)
    if device is None:
        raise exceptions.ValidationError("No pending MFA enrollment found.")

    secret = decrypt_field(device.secret_encrypted)
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=TOTP_VALID_WINDOW):
        raise exceptions.ValidationError("Invalid code. Please try again.")

    # Deliberately does not set last_used_window here: that replay guard
    # exists to stop a login/step-up code from being reused, not to burn
    # the enrollment-confirmation code's window - doing so would leave a
    # brand-new device unable to complete a real login for the rest of
    # that same 30-second window.
    device.is_enabled = True
    device.enrolled_at = timezone.now()
    device.save(update_fields=["is_enabled", "enrolled_at", "updated_datetime"])

    plaintext_codes = _regenerate_recovery_codes(user=user)

    activity_service.log_auth_activity(
        user=user,
        action=UserActivityActionEnums.MFA_ENABLED,
        summary="Enabled MFA.",
        request=request,
    )
    return plaintext_codes


def _regenerate_recovery_codes(*, user: User) -> list[str]:
    MFARecoveryCode.objects.filter(user=user).delete()
    plaintext_codes = [_generate_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    MFARecoveryCode.objects.bulk_create(
        MFARecoveryCode(user=user, code_encrypted=encrypt_field(code))
        for code in plaintext_codes
    )
    return plaintext_codes


def regenerate_recovery_codes(*, user: User, code: str, request=None) -> list[str]:
    """Requires a fresh valid TOTP code as re-auth before burning the old
    codes and issuing new ones."""

    device = get_device(user=user)
    if device is None or not device.is_enabled:
        raise exceptions.ValidationError("MFA is not enabled for this account.")

    secret = decrypt_field(device.secret_encrypted)
    if not pyotp.TOTP(secret).verify(code, valid_window=TOTP_VALID_WINDOW):
        raise exceptions.ValidationError("Invalid code. Please try again.")

    plaintext_codes = _regenerate_recovery_codes(user=user)
    activity_service.log_auth_activity(
        user=user,
        action=UserActivityActionEnums.MFA_RECOVERY_CODES_REGENERATED,
        summary="Regenerated MFA recovery codes.",
        request=request,
    )
    return plaintext_codes


def disable(*, user: User, code: str, request=None) -> None:
    """Self-service disable. Mandatory-MFA roles can never disable outright -
    for them, "reset" means re-enrolling via enroll(), which overwrites the
    secret; this endpoint stays available only for roles MFA isn't
    mandatory for."""

    if user.role in MFA_MANDATED_ROLES:
        raise exceptions.PermissionDenied(
            "MFA is required for this role and cannot be disabled. "
            "Re-enroll to replace your device instead."
        )

    device = get_device(user=user)
    if device is None or not device.is_enabled:
        raise exceptions.ValidationError("MFA is not enabled for this account.")

    secret = decrypt_field(device.secret_encrypted)
    if not pyotp.TOTP(secret).verify(code, valid_window=TOTP_VALID_WINDOW):
        raise exceptions.ValidationError("Invalid code. Please try again.")

    device.delete()
    MFARecoveryCode.objects.filter(user=user).delete()
    activity_service.log_auth_activity(
        user=user,
        action=UserActivityActionEnums.MFA_DISABLED,
        summary="Disabled MFA.",
        request=request,
    )


def admin_reset(*, acting_admin: User, target_user: User, request=None) -> None:
    """Super-Admin-initiated reset for a user who lost their device. Their
    mfa_grace_period_ends_at is left untouched (already set) - this forces
    re-enrollment, not a fresh grace window, so it can't be used to
    perpetually dodge enrollment."""

    MFADevice.objects.filter(user=target_user).delete()
    MFARecoveryCode.objects.filter(user=target_user).delete()
    activity_service.log_activity(
        user=target_user,
        actor_user=acting_admin,
        category=UserActivityCategoryEnums.AUTH,
        action=UserActivityActionEnums.MFA_RESET_BY_ADMIN,
        summary=f"MFA reset by {acting_admin.email}.",
        request=request,
    )


# >>>>>>>>>>>>>>>>>>>> Login challenge <<<<<<<<<<<<<<<<<<<<<<


def create_challenge(*, user: User) -> str:
    """Mint a challenge for the login-time MFA step. Returns the raw,
    client-facing token - only its hash is persisted (EmailVerificationToken's
    own established idiom: never transmit or store a raw guessable id)."""

    raw_token = secrets.token_urlsafe(32)
    MFAChallenge.objects.create(
        user=user,
        challenge_token_hash=_hash_challenge_token(raw_token),
        expires_at=timezone.now() + timedelta(minutes=MFA_CHALLENGE_LIFETIME_MINUTES),
    )
    return raw_token


_GENERIC_MFA_ERROR = "Invalid or expired code."


def verify_challenge(*, challenge_token: str, code: str, request=None) -> User:
    """Verify a login-time MFA challenge. Every failure mode - unknown/
    expired challenge, wrong TOTP, wrong recovery code - collapses into the
    same generic error, mirroring LoginSerializer's deliberate email/password
    error collapsing: this endpoint must never become a side channel for
    guessing live challenge_tokens."""

    challenge = MFAChallenge.objects.filter(
        challenge_token_hash=_hash_challenge_token(challenge_token),
        consumed_at__isnull=True,
    ).first()
    if challenge is None or challenge.expires_at < timezone.now():
        raise exceptions.ValidationError(_GENERIC_MFA_ERROR)

    user = challenge.user
    if is_locked_out(user=user):
        raise exceptions.ValidationError(_GENERIC_MFA_ERROR)

    device = get_device(user=user)
    if device is None or not device.is_enabled:
        raise exceptions.ValidationError(_GENERIC_MFA_ERROR)

    if _verify_totp(device=device, code=code):
        pass
    elif _try_recovery_code(user=user, code=code, request=request):
        pass
    else:
        _register_failed_mfa_attempt(user=user, device=device)
        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.MFA_CHALLENGE_FAILED,
            summary="MFA challenge failed.",
            request=request,
        )
        raise exceptions.ValidationError(_GENERIC_MFA_ERROR)

    challenge.consumed_at = timezone.now()
    challenge.save(update_fields=["consumed_at", "updated_datetime"])
    return user


def _verify_totp(*, device: MFADevice, code: str) -> bool:
    secret = decrypt_field(device.secret_encrypted)
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=TOTP_VALID_WINDOW):
        return False

    current_window = int(time.time() // TOTP_STEP_SECONDS)
    if device.last_used_window is not None and current_window <= device.last_used_window:
        # Replay: a code from an already-consumed (or earlier) window.
        return False

    device.last_used_window = current_window
    device.failed_attempts = 0
    device.save(update_fields=["last_used_window", "failed_attempts", "updated_datetime"])
    return True


def _try_recovery_code(*, user: User, code: str, request=None) -> bool:
    for recovery_code in MFARecoveryCode.objects.filter(user=user, used_at__isnull=True):
        if decrypt_field(recovery_code.code_encrypted) == code:
            recovery_code.used_at = timezone.now()
            recovery_code.save(update_fields=["used_at", "updated_datetime"])
            activity_service.log_auth_activity(
                user=user,
                action=UserActivityActionEnums.MFA_RECOVERY_CODE_USED,
                summary="Used an MFA recovery code.",
                request=request,
            )
            return True
    return False


# >>>>>>>>>>>>>>>>>>>> Login-flow helpers <<<<<<<<<<<<<<<<<<<<<<


def is_within_grace_period(*, user: User) -> bool:
    return bool(
        user.mfa_grace_period_ends_at and user.mfa_grace_period_ends_at > timezone.now()
    )


def verify_fresh_code(*, user: User, code: str) -> bool:
    """Step-up check for sensitive actions (e.g. withdrawal confirmation):
    a fresh TOTP or recovery code, independent of any session claim. Returns
    False rather than raising - callers decide how to surface that."""

    device = get_device(user=user)
    if device is None or not device.is_enabled:
        return False
    if _verify_totp(device=device, code=code):
        return True
    return _try_recovery_code(user=user, code=code)
