from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from api.authentication.services import activity_service
from api.notification.models import Notification
from api.users.enums import UserActivityActionEnums, AccountStatus, UserRole
from api.users.models import User, UserActivityLog
from api.users.permissions import IsAdminOrSuperAdminRole
from api.users.serializers.user_serializer import MeSerializer

MAX_FAILED_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15
#: Second and later lockout within this window escalates to an Admin alert -
#: one lockout is "forgot my password", several in a row looks like an attack.
REPEATED_LOCKOUT_WINDOW_HOURS = 24


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
        invalid_credentials = serializers.ValidationError(
            "Invalid email or password."
        )
        if user is None:
            raise invalid_credentials

        # Reject suspended or deactivated accounts explicitly
        if user.status in (AccountStatus.SUSPENDED, AccountStatus.DEACTIVATED):
            raise serializers.ValidationError(
                {"email": "This account is not active. Please contact support."}
            )

        if user.locked_until and user.locked_until > timezone.now():
            minutes_left = max(
                1, int((user.locked_until - timezone.now()).total_seconds() // 60) + 1
            )
            raise serializers.ValidationError(
                f"Too many failed attempts. Try again in {minutes_left} minute(s)."
            )

        if not user.check_password(attrs["password"]):
            self._register_failed_attempt(user)
            raise invalid_credentials

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

        data = super().validate(attrs)

        # Determine workspace destination based on role
        workspace_map = {
            UserRole.COURSE_CREATOR: "creator_studio",
            UserRole.CREATOR_REVIEWER: "creator_review_dashboard",
            UserRole.STAFF_WRITER: "staff_writer_dashboard",
            UserRole.STAFF_VERIFIER: "staff_verifier_dashboard",
            UserRole.STAFF_APPROVER: "staff_approver_dashboard",
            UserRole.AI_REVIEWER: "ai_review_dashboard",
            UserRole.QA_REVIEWER: "qa_review_dashboard",
            UserRole.ADMIN: "admin_dashboard",
            UserRole.SUPER_ADMIN: "admin_dashboard",
        }
        workspace = workspace_map.get(user.role, "creator_studio")

        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.LOGIN,
            summary="User logged in.",
            request=self.context.get("request"),
        )

        # Enrich response with user info, role and workspace so the frontend can
        # immediately route the user and show session details without an extra API call.
        data.update(
            {
                "user": MeSerializer(user, context=self.context).data,
                "role": user.role,
                "workspace": workspace,
            }
        )
        return data

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

        Notification.emit_email_notification(
            receivers=[user],
            subject="Your account was temporarily locked",
            template_name="emails/account_locked",
            context={
                "first_name": user.first_name,
                "lock_minutes": LOCKOUT_DURATION_MINUTES,
            },
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
