from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import exceptions
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from api.authentication.enums import TokenPurpose
from api.authentication.services import activity_service, token_service
from api.authentication.utils.base_auth import TsesAuthenticationInterface
from api.authentication.utils.links import build_verification_link
from api.notification.models import Notification
from api.users.enums import UserActivityActionEnums, UserRole

User = get_user_model()

EMAIL_VERIFICATION_SUBJECT = "Verify your email"
PASSWORD_RESET_SUBJECT = "Reset your password"


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
        self, *, email: str, password: str, first_name: str, last_name: str
    ) -> User:
        """Create an inactive User and email a signup-verification link.

        Role is always forced to COURSE_CREATOR - not client-settable. The
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
                role=UserRole.COURSE_CREATOR,
                is_active=False,
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
        user.save(update_fields=["is_active"])

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

    def logout(self, *, user: User, refresh_token: str, request=None) -> None:
        """Blacklist `refresh_token` so it can no longer mint new access tokens."""

        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError as exc:
            raise exceptions.ValidationError(
                "Invalid or already blacklisted token."
            ) from exc

        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.LOGOUT,
            summary="User logged out.",
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
