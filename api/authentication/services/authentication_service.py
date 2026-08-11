from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken

from api.authentication.enums import TokenPurpose
from api.authentication.models import UserSession
from api.authentication.services import activity_service, session_service, token_service
from api.authentication.utils.base_auth import TsesAuthenticationInterface
from api.authentication.utils.links import build_verification_link
from api.notification.models import Notification
from api.users.enums import (
    AccountStatus,
    UserActivityActionEnums,
    UserActivityCategoryEnums,
    UserRole,
)

User = get_user_model()

EMAIL_VERIFICATION_SUBJECT = "Verify your email"
PASSWORD_RESET_SUBJECT = "Reset your password"
PASSWORD_RESET_CONFIRMATION_SUBJECT = "Your password was changed"
EMAIL_CHANGE_SUBJECT = "Confirm your new email address"


class AuthenticationService(TsesAuthenticationInterface):
    """Concrete TsesAuthenticationInterface implementation for email/password
    signup, link-based email verification, and password reset.

    The interface's method names (verify_otp, resend_otp) predate the switch
    from typed codes to clickable links and are kept as-is - Python ABCs only
    enforce method presence, not signature, so these now take a `token` kwarg
    instead of `code` internally without violating the contract.

    login() is intentionally not implemented here - it's handled by
    LoginSerializer (a TokenObtainPairSerializer subclass), which already
    provides correct credential verification and the is_active gate via
    simplejwt. This class implements everything TsesAuthenticationInterface
    mandates, plus `logout` and `forgot_password` as additional methods.
    """

    def signup(
        self,
        *,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        country: str,
        terms_accepted: bool = False,
        role: str = UserRole.COURSE_CREATOR,
    ) -> User:
        """Create an inactive User and email a signup-verification link.

        `role` defaults to COURSE_CREATOR and is never taken from client
        input on the public signup endpoint - it's set by which view calls
        this (see ReviewerSignupView, which passes CREATOR_REVIEWER). The
        user stays is_active=False (and therefore cannot authenticate at all,
        per SIMPLE_JWT's USER_AUTHENTICATION_RULE) until verify_otp succeeds.
        """

        if User.objects.filter(email__iexact=email).exists():
            raise exceptions.ValidationError(
                {"email": "A user with this email already exists."}
            )

        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                country=country,
                terms_accepted_at=timezone.now() if terms_accepted else None,
                role=role,
                is_active=False,
            )
            activity_service.log_auth_activity(
                user=user,
                action=UserActivityActionEnums.ACCOUNT_CREATED,
                summary=f"Account created with role {role}.",
            )
            _token, raw_token = token_service.issue_token(
                user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION
            )
            transaction.on_commit(
                lambda: self._send_signup_verification_email(
                    user=user, raw_token=raw_token
                )
            )

        return user

    def verify_otp(self, *, email: str, token: str) -> User:
        """Verify a SIGNUP_VERIFICATION link token and activate the account."""

        user = self._get_user_or_404(email)
        token_service.verify_token(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, token=token
        )

        user.is_active = True
        user.status = AccountStatus.ACTIVE
        user.save(update_fields=["is_active", "status"])

        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.ACCOUNT_VERIFIED,
            summary="Email verified via link.",
        )
        return user

    def generate_access_token(self, *, user: User) -> dict:
        """Issue a fresh access+refresh token pair for `user`."""

        refresh = RefreshToken.for_user(user)
        return {"access": str(refresh.access_token), "refresh": str(refresh)}

    def resend_otp(self, *, email: str, purpose: str) -> None:
        """Re-issue a verification link for `purpose`, subject to the resend cooldown."""

        user = self._get_user_or_404(email)
        if not token_service.can_resend(user=user, purpose=purpose):
            raise exceptions.ValidationError(
                "Please wait before requesting another link."
            )

        _token, raw_token = token_service.issue_token(user=user, purpose=purpose)
        if purpose == TokenPurpose.PASSWORD_RESET:
            self._send_password_reset_email(user=user, raw_token=raw_token)
        else:
            self._send_signup_verification_email(user=user, raw_token=raw_token)

    def reset_password(self, *, email: str, token: str, new_password: str) -> None:
        """Verify a PASSWORD_RESET link token and set a new password."""

        user = self._get_user_or_404(email)
        token_service.verify_token(
            user=user, purpose=TokenPurpose.PASSWORD_RESET, token=token
        )
        validate_password(new_password, user=user)

        with transaction.atomic():
            user.set_password(new_password)
            user.save(update_fields=["password"])
            activity_service.log_auth_activity(
                user=user,
                action=UserActivityActionEnums.PASSWORD_RESET_COMPLETED,
                summary="Password reset via link.",
            )
            self.logout_all_sessions(user=user)

        self._send_password_reset_confirmation_email(user=user)

    def change_password(
        self, *, user: User, current_password: str, new_password: str
    ) -> None:
        """Change a logged-in user's password after confirming the current one.

        Known limitation: does not blacklist the user's other outstanding
        refresh tokens - only single-token blacklisting on explicit /logout/
        exists in this codebase, so other active sessions keep working after
        this call. Flagged, not solved here.
        """

        if not user.check_password(current_password):
            raise exceptions.ValidationError(
                {"current_password": "Current password is incorrect."}
            )
        validate_password(new_password, user=user)

        user.set_password(new_password)
        user.save(update_fields=["password"])
        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.PASSWORD_CHANGED,
            summary="Password changed.",
        )

    def request_email_change(
        self, *, user: User, new_email: str, password: str
    ) -> None:
        """Start a two-step email change: verify identity, then email a
        confirmation link to the NEW address. User.email is not changed yet -
        only confirm_email_change() applies it, once the new inbox proves
        ownership."""

        if not user.check_password(password):
            raise exceptions.ValidationError({"password": "Incorrect password."})
        if User.objects.filter(email__iexact=new_email).exists():
            raise exceptions.ValidationError(
                {"new_email": "A user with this email already exists."}
            )

        _token, raw_token = token_service.issue_token(
            user=user,
            purpose=TokenPurpose.EMAIL_CHANGE,
            extra_fields={"new_email": new_email},
        )
        link = build_verification_link(
            path="/change-email", email=new_email, token=raw_token
        )
        Notification.emit_email_notification(
            receivers=[new_email],
            subject=EMAIL_CHANGE_SUBJECT,
            template_name="emails/email_change_confirmation",
            context={"first_name": user.first_name, "confirmation_link": link},
        )

    def confirm_email_change(self, *, token: str) -> User:
        """Verify an EMAIL_CHANGE token and apply its pending new_email to
        the owning user. Resolves the user from the token itself - the
        confirming link is opened from the new inbox, where the caller may
        not be authenticated."""

        record = token_service.verify_token_without_user(
            purpose=TokenPurpose.EMAIL_CHANGE, token=token
        )
        user = record.user
        user.email = record.new_email
        user.save(update_fields=["email"])
        activity_service.log_activity(
            user=user,
            category=UserActivityCategoryEnums.CONFIGURATION,
            action=UserActivityActionEnums.PROFILE_UPDATED,
            summary="Email address changed.",
        )
        return user

    def logout(self, *, user: User, refresh_token: str, request=None) -> None:
        """Blacklist `refresh_token` so it can no longer mint new access tokens."""

        try:
            token = RefreshToken(refresh_token)
            sid = token.get("sid")
            token.blacklist()
        except TokenError as exc:
            raise exceptions.ValidationError(
                "Invalid or already blacklisted token."
            ) from exc

        if sid:
            try:
                session_service.revoke_session(user=user, session_id=sid)
            except UserSession.DoesNotExist:
                # Already revoked (e.g. via the sessions list) or predates
                # this feature - logout itself still succeeded above.
                pass

        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.LOGOUT,
            summary="User logged out.",
            request=request,
        )

    def logout_all_sessions(self, *, user: User, request=None) -> None:
        """Blacklist every outstanding refresh token for `user`, ending every
        session on every device. Already-issued access tokens keep working
        until they naturally expire (same limitation as single-token logout -
        access tokens aren't individually revocable, only refresh tokens are
        tracked for blacklisting)."""

        outstanding = OutstandingToken.objects.filter(user=user)
        BlacklistedToken.objects.bulk_create(
            (BlacklistedToken(token=token) for token in outstanding),
            ignore_conflicts=True,
        )
        session_service.revoke_all_sessions(user=user)

        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.SESSIONS_REVOKED,
            summary="Logged out of all devices.",
            request=request,
        )

    def forgot_password(self, *, email: str) -> None:
        """Issue a PASSWORD_RESET link if `email` matches a user.

        Never reveals whether the email exists - returns None either way.
        """

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return

        _token, raw_token = token_service.issue_token(
            user=user, purpose=TokenPurpose.PASSWORD_RESET
        )
        self._send_password_reset_email(user=user, raw_token=raw_token)

    @staticmethod
    def _get_user_or_404(email: str) -> User:
        try:
            return User.objects.get(email__iexact=email)
        except User.DoesNotExist as exc:
            raise exceptions.NotFound("No user found with this email.") from exc

    @staticmethod
    def _send_signup_verification_email(*, user: User, raw_token: str) -> None:
        link = build_verification_link(
            path="/verify-email", email=user.email, token=raw_token
        )
        Notification.emit_email_notification(
            receivers=[user],
            subject=EMAIL_VERIFICATION_SUBJECT,
            template_name="emails/email_verification",
            context={
                "first_name": user.first_name,
                "verification_link": link,
                "expiry_minutes": settings.EMAIL_TOKEN_EXPIRY_MINUTES,
            },
        )

    @staticmethod
    def _send_password_reset_email(*, user: User, raw_token: str) -> None:
        link = build_verification_link(
            path="/reset-password", email=user.email, token=raw_token
        )
        Notification.emit_email_notification(
            receivers=[user],
            subject=PASSWORD_RESET_SUBJECT,
            template_name="emails/password_reset",
            context={
                "first_name": user.first_name,
                "reset_link": link,
                "expiry_minutes": settings.EMAIL_TOKEN_EXPIRY_MINUTES,
            },
        )

    @staticmethod
    def _send_password_reset_confirmation_email(*, user: User) -> None:
        Notification.emit_email_notification(
            receivers=[user],
            subject=PASSWORD_RESET_CONFIRMATION_SUBJECT,
            template_name="emails/password_reset_confirmation",
            context={"first_name": user.first_name},
        )


#: Destination the frontend routes to immediately after login, by role.
_WORKSPACE_BY_ROLE = {
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


def finish_login(*, user: User, request=None, mfa_verified: bool) -> dict:
    """Shared tail of a successful login, called once the caller has already
    decided the account is fully authenticated - either LoginSerializer
    (password-only, no MFA required or not yet enrolled) or the MFA-verify
    endpoint (password + a passed challenge). Mints tokens, creates the
    UserSession, and assembles the same response shape either way, so a
    client can't tell which path was taken from the shape of the response.

    `mfa_verified` is embedded as a token claim consumed by
    IsMFAVerifiedForSession - True only when this login actually completed
    an MFA challenge (or the role never required one), never merely because
    the account happens to be inside its enrollment grace period.

    Local import of LoginSerializer avoids a module-level import cycle:
    login_serializer.py imports this module already.
    """

    from api.authentication.serializers.login_serializer import LoginSerializer
    from api.users.serializers.user_serializer import MeSerializer

    refresh = LoginSerializer.get_token(user)
    session = session_service.create_session(
        user=user, refresh_jti=refresh["jti"], request=request
    )
    refresh["sid"] = str(session.id)
    refresh["mfa_verified"] = mfa_verified
    access = refresh.access_token

    activity_service.log_auth_activity(
        user=user,
        action=UserActivityActionEnums.LOGIN,
        summary="User logged in.",
        request=request,
    )

    return {
        "refresh": str(refresh),
        "access": str(access),
        "user": MeSerializer(user).data,
        "role": user.role,
        "workspace": _WORKSPACE_BY_ROLE.get(user.role, "creator_studio"),
    }
