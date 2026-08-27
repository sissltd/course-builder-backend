import logging
from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from api.authentication.services import (
    activity_service,
    authentication_service,
    mfa_service,
)
from api.notification.models import Notification
from api.users.enums import UserActivityActionEnums, AccountStatus
from api.users.models import User, UserActivityLog
from api.users.permissions import IsAdminOrSuperAdminRole

MAX_FAILED_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15
#: Second and later lockout within this window escalates to an Admin alert -
#: one lockout is "forgot my password", several in a row looks like an attack.
REPEATED_LOCKOUT_WINDOW_HOURS = 24
logger = logging.getLogger(__name__)


class LoginSerializer(TokenObtainPairSerializer):
    """Adds a UserActivityLog(action=LOGIN) write, account lockout after
    repeated failures, and a generic non-enumerating error for
    wrong-email/wrong-password (superseding an earlier deliberate tradeoff
    that reported those two cases distinctly).

    No new input fields: TokenObtainPairSerializer already exposes
    User.USERNAME_FIELD ("email") + "password".
    """

    @classmethod
    def get_token(cls, user):
        """Attach the user's role to the token claims so clients and APIs
        can reason about role without a separate user lookup.
        """
        token = super().get_token(user)
        token["role"] = user.role
        return token

    def validate(self, attrs):
        user = User.objects.filter(email__iexact=attrs[self.username_field]).first()

        # Wrong email and wrong password are reported identically (a
        # non-field error) so neither response tells an attacker which part
        # was wrong - the classic enumeration vector this replaces.
        invalid_credentials = serializers.ValidationError("Invalid email or password.")
        if user is None:
            raise invalid_credentials

        # A lock that has already expired clears itself here rather than
        # waiting for a *successful* login - otherwise failed_login_attempts
        # stays pinned at MAX_FAILED_LOGIN_ATTEMPTS forever, and the very
        # next wrong password (e.g. from someone who still doesn't remember
        # it) re-locks the account instantly instead of getting a fresh
        # budget of attempts.
        if user.locked_until and user.locked_until <= timezone.now():
            user.failed_login_attempts = 0
            user.locked_until = None
            user.save(update_fields=["failed_login_attempts", "locked_until"])

        currently_locked = bool(
            user.locked_until and user.locked_until > timezone.now()
        )

        # The password check happens before any account-status check, and
        # every status (suspended/deactivated, locked out, unknown email) is
        # reported as the same generic invalid_credentials on a wrong
        # password - only a *correct* password unlocks the more specific
        # status message below. Checking status first (the previous
        # ordering) let an attacker who doesn't have the password still
        # learn that an email is registered, suspended, or locked out, which
        # defeated the point of the generic message above.
        if not user.check_password(attrs["password"]):
            if not currently_locked:
                self._register_failed_attempt(user)
            raise invalid_credentials

        if user.status in (AccountStatus.SUSPENDED, AccountStatus.DEACTIVATED):
            raise serializers.ValidationError(
                {"email": "This account is not active. Please contact support."}
            )

        if currently_locked:
            minutes_left = max(
                1, int((user.locked_until - timezone.now()).total_seconds() // 60) + 1
            )
            raise serializers.ValidationError(
                f"Too many failed attempts. Try again in {minutes_left} minute(s)."
            )

        if not user.is_active:
            raise serializers.ValidationError(
                {
                    "email": "This account has not been verified yet. Please check your email for a verification link."
                }
            )

        # Reset failed login attempts and any lock on successful authentication
        user.failed_login_attempts = 0
        user.locked_until = None

        # Update last_login timestamp
        user.last_login = timezone.now()
        user.save(update_fields=["failed_login_attempts", "locked_until", "last_login"])

        request = self.context.get("request")

        # MFA gate - only ADMIN/SUPER_ADMIN are ever required to have it.
        # Everyone else's password check above is the entire login.
        if user.role in mfa_service.MFA_MANDATED_ROLES:
            if mfa_service.is_mfa_enabled(user=user):
                # Do not mint tokens yet - hand back a challenge instead.
                # POST /auth/mfa/verify/ completes the login on success.
                challenge_token = mfa_service.create_challenge(user=user)
                return {"mfa_required": True, "challenge_token": challenge_token}

            data = authentication_service.finish_login(
                user=user, request=request, mfa_verified=False
            )
            if mfa_service.is_within_grace_period(user=user):
                data["mfa_enrollment_required"] = True
                data["mfa_grace_period_ends_at"] = user.mfa_grace_period_ends_at
            else:
                # Grace period has lapsed - login itself still succeeds (no
                # support-desk lockout spiral), but mfa_verified=False means
                # IsMFAVerifiedForSession-gated actions stay blocked until
                # they enroll.
                data["mfa_enrollment_overdue"] = True
            return data

        return authentication_service.finish_login(
            user=user, request=request, mfa_verified=True
        )

    def _register_failed_attempt(self, user: User) -> None:
        """Increment failed_login_attempts and, at the threshold, lock the
        account, log a LOCKOUT_TRIGGERED entry, email the account owner, and
        (on a 2nd+ lockout within REPEATED_LOCKOUT_WINDOW_HOURS) alert Admins
        in-app - a single lockout is routine; repeated ones look like an
        attack in progress.
        """

        request = self.context.get("request")
        user.failed_login_attempts += 1

        if user.failed_login_attempts < MAX_FAILED_LOGIN_ATTEMPTS:
            user.save(update_fields=["failed_login_attempts"])
            activity_service.log_auth_activity(
                user=user,
                action=UserActivityActionEnums.LOGIN,
                summary="Failed login attempt: incorrect password.",
                request=request,
            )
            return

        user.locked_until = timezone.now() + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
        user.save(update_fields=["failed_login_attempts", "locked_until"])
        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.LOCKOUT_TRIGGERED,
            summary=f"Account locked for {LOCKOUT_DURATION_MINUTES} minutes after "
            f"{MAX_FAILED_LOGIN_ATTEMPTS} failed login attempts.",
            request=request,
        )

        from api.notification.services.email_service import send_templated_email

        try:
            sent_count = send_templated_email(
                receivers=[user.email],
                subject="Your account was temporarily locked",
                template_name="emails/account_locked",
                context={
                    "first_name": user.first_name,
                    "lock_minutes": LOCKOUT_DURATION_MINUTES,
                },
            )
            logger.info(
                "auth_email_sent email_type=ACCOUNT_LOCKED recipients=%s subject=%s "
                "sent_count=%s",
                [user.email],
                "Your account was temporarily locked",
                sent_count,
            )
        except Exception:
            logger.exception(
                "auth_email_failed email_type=ACCOUNT_LOCKED recipients=%s subject=%s",
                [user.email],
                "Your account was temporarily locked",
            )

        window_start = timezone.now() - timedelta(hours=REPEATED_LOCKOUT_WINDOW_HOURS)
        lockout_count = UserActivityLog.objects.filter(
            user=user,
            action=UserActivityActionEnums.LOCKOUT_TRIGGERED,
            activity_datetime__gte=window_start,
        ).count()
        if lockout_count >= 2:
            admins = User.objects.filter(role__in=IsAdminOrSuperAdminRole.allowed_roles)
            Notification.emit_in_app_notification(
                receivers=list(admins),
                title="Repeated login lockouts",
                content=(
                    f"{user.email} has been locked out {lockout_count} times in "
                    f"the last {REPEATED_LOCKOUT_WINDOW_HOURS} hours."
                ),
                metadata={"user_id": user.id, "lockout_count": lockout_count},
            )
