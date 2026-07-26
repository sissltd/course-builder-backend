from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.authentication.serializers import (
    ChangeEmailConfirmSerializer,
    ChangeEmailRequestSerializer,
    ChangePasswordSerializer,
    ForgotPasswordSerializer,
    LoginSerializer,
    LogoutSerializer,
    ResendVerificationSerializer,
    ResetPasswordSerializer,
    SignupSerializer,
    VerifyEmailSerializer,
)
from api.authentication.services.authentication_service import AuthenticationService
from api.users.enums import UserRole
from api.users.serializers import MeSerializer

auth_service = AuthenticationService()


class SignupView(APIView):
    """Create an inactive user and email a signup-verification link."""

    permission_classes = [AllowAny]
    serializer_class = (
        SignupSerializer  # for schema generation only; not a GenericAPIView
    )
    signup_role = UserRole.COURSE_CREATOR

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = auth_service.signup(**serializer.validated_data, role=self.signup_role)
        return Response(MeSerializer(user).data, status=201)


class ReviewerSignupView(SignupView):
    """Same signup flow as SignupView, forcing role=CREATOR_REVIEWER instead.

    A separate endpoint (not a role field on the shared /signup/) per
    explicit product decision - open self-service, not admin-invite-gated.
    """

    signup_role = UserRole.CREATOR_REVIEWER


class VerifyEmailView(APIView):
    """Verify a signup link token, activate the account, and auto-issue tokens."""

    permission_classes = [AllowAny]
    serializer_class = (
        VerifyEmailSerializer  # for schema generation only; not a GenericAPIView
    )

    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = auth_service.verify_otp(**serializer.validated_data)
        tokens = auth_service.generate_access_token(user=user)
        return Response({**tokens, "user": MeSerializer(user).data}, status=200)


class ResendVerificationView(APIView):
    """Re-issue a verification link for signup verification or password reset."""

    permission_classes = [AllowAny]
    serializer_class = (
        ResendVerificationSerializer  # for schema generation only; not a GenericAPIView
    )

    def post(self, request):
        serializer = ResendVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        auth_service.resend_otp(**serializer.validated_data)
        return Response(
            {"detail": "A new verification link has been sent."}, status=200
        )


class LoginView(APIView):
    """Exchange email+password for a JWT access/refresh token pair."""

    permission_classes = [AllowAny]
    serializer_class = (
        LoginSerializer  # for schema generation only; not a GenericAPIView
    )

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=200)


class LogoutView(APIView):
    """Blacklist a refresh token, ending the session it belongs to."""

    permission_classes = [IsAuthenticated]
    serializer_class = (
        LogoutSerializer  # for schema generation only; not a GenericAPIView
    )

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        auth_service.logout(
            user=request.user,
            refresh_token=serializer.validated_data["refresh"],
            request=request,
        )
        return Response({"detail": "Logged out."}, status=200)


class ForgotPasswordView(APIView):
    """Request a password-reset link. Never reveals whether the email exists."""

    permission_classes = [AllowAny]
    serializer_class = (
        ForgotPasswordSerializer  # for schema generation only; not a GenericAPIView
    )

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        auth_service.forgot_password(**serializer.validated_data)
        return Response(
            {
                "detail": "If an account exists for this email, a reset link has been sent."
            },
            status=200,
        )


class ResetPasswordView(APIView):
    """Consume a password-reset link token and set a new password."""

    permission_classes = [AllowAny]
    serializer_class = (
        ResetPasswordSerializer  # for schema generation only; not a GenericAPIView
    )

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        auth_service.reset_password(**serializer.validated_data)
        return Response({"detail": "Password reset successfully."}, status=200)


class ChangePasswordView(APIView):
    """Change the logged-in user's password (current password required)."""

    permission_classes = [IsAuthenticated]
    serializer_class = (
        ChangePasswordSerializer  # for schema generation only; not a GenericAPIView
    )

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        auth_service.change_password(user=request.user, **serializer.validated_data)
        return Response({"detail": "Password changed successfully."}, status=200)


class ChangeEmailRequestView(APIView):
    """Request an email change - emails a confirmation link to the new address."""

    permission_classes = [IsAuthenticated]
    serializer_class = (
        ChangeEmailRequestSerializer  # for schema generation only; not a GenericAPIView
    )

    def post(self, request):
        serializer = ChangeEmailRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        auth_service.request_email_change(user=request.user, **serializer.validated_data)
        return Response(
            {"detail": "A confirmation link has been sent to your new email address."},
            status=200,
        )


class ChangeEmailConfirmView(APIView):
    """Consume an email-change confirmation link token and apply the new email.

    AllowAny: the confirming link is opened from the new inbox, where the
    caller may not have an active session at all - identity was already
    proven at the request step (current password), the token proves control
    of the new inbox.
    """

    permission_classes = [AllowAny]
    serializer_class = (
        ChangeEmailConfirmSerializer  # for schema generation only; not a GenericAPIView
    )

    def post(self, request):
        serializer = ChangeEmailConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        auth_service.confirm_email_change(**serializer.validated_data)
        return Response({"detail": "Email address changed successfully."}, status=200)
