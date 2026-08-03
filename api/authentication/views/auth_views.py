from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView as SimpleJWTTokenRefreshView

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
    TokenRefreshSerializer,
    VerifyEmailSerializer,
)
from api.authentication.services.authentication_service import AuthenticationService
from api.users.enums import UserRole
from api.users.serializers import MeSerializer
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

auth_service = AuthenticationService()

_ME_EXAMPLE = {
    "id": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    "email": "jane.doe@example.com",
    "first_name": "Jane",
    "last_name": "Doe",
    "country": "NG",
    "timezone": "Africa/Lagos",
    "avatar_url": "",
    "terms_accepted_at": "2026-07-12T09:30:11.204Z",
    "role": "COURSE_CREATOR",
    "is_active": False,
    "status": "PENDING",
    "created_datetime": "2026-07-12T09:30:11.204Z",
    "updated_datetime": "2026-07-12T09:30:11.204Z",
    "has_completed_onboarding": False,
}

_TOKEN_PAIR_EXAMPLE = {
    "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIn0.7Qm2",
    "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCJ9.9Lp4",
    "user": {**_ME_EXAMPLE, "is_active": True, "status": "ACTIVE"},
}

_DETAIL_SCHEMA = {"type": "object", "properties": {"detail": {"type": "string"}}}

_TOKEN_PAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "access": {"type": "string"},
        "refresh": {"type": "string"},
        "user": {"type": "object"},
    },
}


class SignupView(APIView):
    """Create an inactive user and email a signup-verification link."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "signup"
    serializer_class = (
        SignupSerializer  # for schema generation only; not a GenericAPIView
    )
    signup_role = UserRole.COURSE_CREATOR

    @extend_schema(
        summary="Sign up as a Course Creator",
        description=(
            "Creates an inactive user account and emails a signup-"
            "verification link. The account cannot authenticate until the "
            "link is followed and `POST /api/v1/auth/verify-email/` "
            "succeeds.\n\n"
            "Called from the public signup form.\n\n"
            "**Auth:** Public.\n\n"
            "**Prerequisites:** The email must not already belong to an "
            "existing user.\n\n"
            "**Important:** `role` is never taken from the request - this "
            "endpoint always creates a `COURSE_CREATOR`. Use "
            "`/api/v1/auth/signup/reviewer/` for the Creator Reviewer "
            "self-signup flow instead. Staff roles cannot be created "
            "through any public signup route - see "
            "`/api/v1/auth/staff/invitations/`."
        ),
        tags=["Auth — Signup & Verification"],
        request=SignupSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "email": "jane.doe@example.com",
                    "password": "Rw4$eTn8Kp2q",
                    "password_confirm": "Rw4$eTn8Kp2q",
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "country": "NG",
                    "terms_accepted": True,
                },
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=MeSerializer,
                description="Account created, inactive until verified.",
                examples=[OpenApiExample(name="Success", value=_ME_EXAMPLE)],
            ),
            400: OpenApiResponse(
                description="The email is taken, passwords don't match, or the password is too weak.",
                examples=[
                    OpenApiExample(
                        name="Email already taken",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "A user with this email already exists.",
                                    "field_name": "email",
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Passwords don't match",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "Passwords do not match.",
                                    "field_name": "password_confirm",
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
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

    @extend_schema(
        summary="Sign up as a Creator Reviewer",
        description=(
            "Identical to `POST /api/v1/auth/signup/`, forcing "
            "`role=CREATOR_REVIEWER` instead of `COURSE_CREATOR`. A "
            "separate endpoint rather than a `role` field on the shared "
            "signup form, per product decision - Creator Reviewer is open "
            "self-service, not admin-invite-gated like the Verifier/"
            "Approver/Writer staff roles.\n\n"
            "Called from the public 'Sign up as a Reviewer' form.\n\n"
            "**Auth:** Public.\n\n"
            "**Prerequisites:** The email must not already belong to an "
            "existing user.\n\n"
            "**Important:** Otherwise behaves exactly like `SignupView` - "
            "same verification-link flow, same validation."
        ),
        tags=["Auth — Signup & Verification"],
        request=SignupSerializer,
        responses={
            201: OpenApiResponse(
                response=MeSerializer,
                description="Account created, inactive until verified.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={**_ME_EXAMPLE, "role": "CREATOR_REVIEWER"},
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        return super().post(request)


class VerifyEmailView(APIView):
    """Verify a signup link token, activate the account, and auto-issue tokens."""

    permission_classes = [AllowAny]
    serializer_class = (
        VerifyEmailSerializer  # for schema generation only; not a GenericAPIView
    )

    @extend_schema(
        summary="Verify a signup email link",
        description=(
            "Consumes the token from a signup-verification link, activates "
            "the account, and returns a JWT pair so the new user is signed "
            "in immediately - no separate login round-trip.\n\n"
            "Called when the user opens the verification link emailed by "
            "`/api/v1/auth/signup/`.\n\n"
            "**Auth:** Public - the token is the credential.\n\n"
            "**Prerequisites:** A signup verification link must have been "
            "issued for this email and not yet used or expired.\n\n"
            "**Important:** The token is single-use; replaying this call "
            "returns 404. Wrong token, unknown email, or an already-"
            "verified account all fail the same way. If the link expired, "
            "use `/api/v1/auth/resend-verification/` to get a new one."
        ),
        tags=["Auth — Signup & Verification"],
        request=VerifyEmailSerializer,
        responses={
            200: OpenApiResponse(
                response=_TOKEN_PAIR_SCHEMA,
                description="Account verified and signed in.",
                examples=[OpenApiExample(name="Success", value=_TOKEN_PAIR_EXAMPLE)],
            ),
            400: OpenApiResponse(
                description="The token has expired, or too many attempts were made.",
                examples=[
                    OpenApiExample(
                        name="Code expired",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "This code has expired. Please request a new one.",
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            404: OpenApiResponse(
                description="No matching unused token for this email.",
                examples=[
                    OpenApiExample(
                        name="Invalid token",
                        value={
                            "errors": [
                                {
                                    "type": "client_error",
                                    "code": "not_found",
                                    "message": (
                                        "Invalid or expired verification "
                                        "code. Please request a new one."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
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

    @extend_schema(
        summary="Resend a verification link",
        description=(
            "Re-issues a link for the given `purpose`, invalidating any "
            "previous unused one for that email+purpose. One endpoint "
            "covers both signup verification and password reset, since "
            "both are the same underlying token mechanism.\n\n"
            "Called from a 'Resend link' / 'Didn't get the email?' "
            "action.\n\n"
            "**Auth:** Public.\n\n"
            "**Prerequisites:** A user must exist for the given email.\n\n"
            "**Important:** Subject to a resend cooldown - calling this "
            "again immediately after a previous send returns 400. This "
            "endpoint does reveal whether the email exists (via 404), "
            "unlike `/api/v1/auth/forgot-password/`, which never does."
        ),
        tags=["Auth — Signup & Verification"],
        request=ResendVerificationSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "email": "jane.doe@example.com",
                    "purpose": "SIGNUP_VERIFICATION",
                },
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=_DETAIL_SCHEMA,
                description="A new link has been sent.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={"detail": "A new verification link has been sent."},
                    )
                ],
            ),
            400: OpenApiResponse(
                description="The resend cooldown is still running.",
                examples=[
                    OpenApiExample(
                        name="Cooldown active",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "Please wait before requesting "
                                        "another link."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            404: STANDARD_ERROR_RESPONSES["not_found"][404],
            **STANDARD_ERROR_RESPONSES["server"],
        },
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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    serializer_class = (
        LoginSerializer  # for schema generation only; not a GenericAPIView
    )

    @extend_schema(
        summary="Log in",
        description=(
            "Exchanges email + password for a JWT access/refresh token "
            "pair, and enriches the response with the caller's profile, "
            "role, and the frontend workspace to route them to - so the "
            "frontend can render the right dashboard without a second "
            "call.\n\n"
            "Called from the login form.\n\n"
            "**Auth:** Public.\n\n"
            "**Prerequisites:** The account must exist, be active/"
            "verified, and not suspended or deactivated.\n\n"
            "**Important:** Wrong email, wrong password, and an "
            "unverified account are reported as distinct field-scoped "
            "errors (a deliberate choice favoring specific feedback over "
            "anti-enumeration) - do not rely on this endpoint hiding "
            "whether an email is registered. `workspace` tells the "
            "frontend which dashboard to route to based on role; `role` is "
            "also embedded in the access token's claims."
        ),
        tags=["Auth — Session"],
        request=LoginSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={"email": "jane.doe@example.com", "password": "Rw4$eTn8Kp2q"},
            ),
        ],
        responses={
            200: OpenApiResponse(
                description="Logged in.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            **_TOKEN_PAIR_EXAMPLE,
                            "role": "COURSE_CREATOR",
                            "workspace": "creator_studio",
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                description="Wrong email, wrong password, unverified account, or suspended/deactivated account.",
                examples=[
                    OpenApiExample(
                        name="No account for email",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "No account found with this email address.",
                                    "field_name": "email",
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Incorrect password",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "Incorrect password.",
                                    "field_name": "password",
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Not verified",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "This account has not been verified "
                                        "yet. Please check your email for a "
                                        "verification link."
                                    ),
                                    "field_name": "email",
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
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

    @extend_schema(
        summary="Log out",
        description=(
            "Blacklists the supplied refresh token so it can no longer "
            "mint new access tokens, ending that session.\n\n"
            "Called from the 'Log out' action.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** `refresh` must be the token issued to the "
            "current session (or any still-valid refresh token for this "
            "user).\n\n"
            "**Important:** Only the supplied token is blacklisted - other "
            "active sessions (other devices/tabs) are unaffected. The "
            "already-issued access token also keeps working until it "
            "naturally expires; this endpoint does not revoke access "
            "tokens, only refresh tokens."
        ),
        tags=["Auth — Session"],
        request=LogoutSerializer,
        responses={
            200: OpenApiResponse(
                response=_DETAIL_SCHEMA,
                description="Logged out.",
                examples=[OpenApiExample(name="Success", value={"detail": "Logged out."})],
            ),
            400: OpenApiResponse(
                description="The refresh token is invalid or already blacklisted.",
                examples=[
                    OpenApiExample(
                        name="Invalid token",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "Invalid or already blacklisted token.",
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
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


class LogoutAllView(APIView):
    """Blacklist every refresh token belonging to the authenticated user,
    ending every session on every device (not just the caller's)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Log out of all devices",
        description=(
            "Blacklists every outstanding refresh token for the "
            "authenticated user, ending every session on every device - "
            "not just the one making this call.\n\n"
            "Called from a 'Log out of all devices' security action, e.g. "
            "after a user suspects their account was compromised.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None - acts on the calling user's own "
            "account, no request body needed.\n\n"
            "**Important:** Already-issued access tokens are not "
            "individually revocable (only refresh tokens are tracked for "
            "blacklisting), so each device's current access token keeps "
            "working until it naturally expires - same limitation as "
            "single-device `/auth/logout/`, just applied to every "
            "refresh token at once."
        ),
        tags=["Auth — Session"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=_DETAIL_SCHEMA,
                description="Logged out of all devices.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={"detail": "Logged out of all devices."},
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        auth_service.logout_all_sessions(user=request.user, request=request)
        return Response({"detail": "Logged out of all devices."}, status=200)


class TokenRefreshView(SimpleJWTTokenRefreshView):
    """Same endpoint as SimpleJWT's stock TokenRefreshView, with
    TokenRefreshSerializer swapped in so a suspended/deactivated user's
    still-valid refresh token can no longer mint new access tokens."""

    serializer_class = TokenRefreshSerializer

    @extend_schema(
        summary="Refresh an access token",
        description=(
            "Exchanges a valid, non-blacklisted refresh token for a new "
            "access token (and, since `ROTATE_REFRESH_TOKENS` is enabled, a "
            "new refresh token - the old one is blacklisted immediately, so "
            "reusing it fails).\n\n"
            "Called by the client's HTTP layer whenever an access token "
            "has expired.\n\n"
            "**Auth:** None - the refresh token itself is the credential.\n\n"
            "**Prerequisites:** `refresh` must be a still-valid, "
            "non-blacklisted refresh token.\n\n"
            "**Important:** Rejects the refresh (401) if the token's "
            "owning user has since been suspended or deactivated, even "
            "though the token itself is still cryptographically valid - "
            "this is checked against the live user row on every refresh, "
            "not just at login."
        ),
        tags=["Auth — Session"],
        responses={
            200: OpenApiResponse(
                description="A new access (and refresh) token.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                            "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                        },
                    )
                ],
            ),
            401: OpenApiResponse(
                description=(
                    "The refresh token is invalid, expired, blacklisted, or "
                    "its owning user is suspended/deactivated."
                ),
                examples=[
                    OpenApiExample(
                        name="Account not active",
                        value={
                            "detail": "This account is not active. Please contact support.",
                            "code": "token_not_valid",
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class ForgotPasswordView(APIView):
    """Request a password-reset link. Never reveals whether the email exists."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "forgot_password"
    serializer_class = (
        ForgotPasswordSerializer  # for schema generation only; not a GenericAPIView
    )

    @extend_schema(
        summary="Request a password reset link",
        description=(
            "Emails a password-reset link if the given email matches an "
            "account. Always returns the same success response regardless "
            "of whether a match was found, so this endpoint cannot be used "
            "to enumerate registered emails.\n\n"
            "Called from the 'Forgot password?' link on the login form.\n\n"
            "**Auth:** Public.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Unlike `/api/v1/auth/resend-verification/`, "
            "this never 404s - a non-existent email silently does nothing "
            "on the backend while still returning 200, by design."
        ),
        tags=["Auth — Password"],
        request=ForgotPasswordSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={"email": "jane.doe@example.com"},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=_DETAIL_SCHEMA,
                description="Always returned, whether or not the email exists.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "detail": (
                                "If an account exists for this email, a "
                                "reset link has been sent."
                            )
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "reset_password"
    serializer_class = (
        ResetPasswordSerializer  # for schema generation only; not a GenericAPIView
    )

    @extend_schema(
        summary="Reset a forgotten password",
        description=(
            "Consumes the token from a password-reset link and sets a new "
            "password. Does not sign the user in - they log in separately "
            "afterwards with the new password.\n\n"
            "Called when the user opens the reset link emailed by "
            "`/api/v1/auth/forgot-password/` and submits a new password.\n\n"
            "**Auth:** Public - the token is the credential.\n\n"
            "**Prerequisites:** A password-reset link must have been "
            "issued for this email and not yet used or expired.\n\n"
            "**Important:** The token is single-use; replaying this call "
            "returns 404. `new_password` goes through Django's standard "
            "password validators (length, common-password checks, etc.)."
        ),
        tags=["Auth — Password"],
        request=ResetPasswordSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "email": "jane.doe@example.com",
                    "token": "8Kj2mNqR7vXyB4dW1sHfL6pT0aZcE3gU9nY5bV8rQmI",
                    "new_password": "Rw4$eTn8Kp2q",
                },
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=_DETAIL_SCHEMA,
                description="Password reset.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={"detail": "Password reset successfully."},
                    )
                ],
            ),
            400: OpenApiResponse(
                description="The token has expired, too many attempts were made, or the new password is too weak.",
                examples=[
                    OpenApiExample(
                        name="Code expired",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "This code has expired. Please request a new one.",
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Weak password",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "password_too_short",
                                    "message": (
                                        "This password is too short. It "
                                        "must contain at least 8 characters."
                                    ),
                                    "field_name": "new_password",
                                }
                            ]
                        },
                    ),
                ],
            ),
            404: OpenApiResponse(
                description="No matching unused token for this email.",
                examples=[
                    OpenApiExample(
                        name="Invalid token",
                        value={
                            "errors": [
                                {
                                    "type": "client_error",
                                    "code": "not_found",
                                    "message": (
                                        "Invalid or expired verification "
                                        "code. Please request a new one."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
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

    @extend_schema(
        summary="Change password",
        description=(
            "Changes the logged-in user's password, after confirming they "
            "know the current one.\n\n"
            "Called from the account settings 'Change password' form.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** `current_password` must match the "
            "account's actual current password.\n\n"
            "**Important:** Known limitation - this does not blacklist the "
            "user's other outstanding refresh tokens, only single-token "
            "blacklisting via `/api/v1/auth/logout/` exists, so other "
            "active sessions on other devices keep working after this "
            "call."
        ),
        tags=["Auth — Password"],
        request=ChangePasswordSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "current_password": "Rw4$eTn8Kp2q",
                    "new_password": "Kt7#pQz2Lm9v",
                },
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=_DETAIL_SCHEMA,
                description="Password changed.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={"detail": "Password changed successfully."},
                    )
                ],
            ),
            400: OpenApiResponse(
                description="The current password is wrong, or the new password is too weak.",
                examples=[
                    OpenApiExample(
                        name="Wrong current password",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "Current password is incorrect.",
                                    "field_name": "current_password",
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
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

    @extend_schema(
        summary="Request an email change",
        description=(
            "Starts a two-step email change: verifies the caller's "
            "identity via their current password, then emails a "
            "confirmation link to the *new* address. `User.email` is not "
            "changed yet - only confirming the link applies it, once the "
            "new inbox proves ownership.\n\n"
            "Called from the account settings 'Change email' form.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** `password` must match the account's "
            "current password; `new_email` must not already belong to "
            "another user.\n\n"
            "**Important:** The current session and login email keep "
            "working until the new address is confirmed via "
            "`/api/v1/auth/change-email/confirm/` - nothing changes on "
            "this call alone."
        ),
        tags=["Auth — Email Change"],
        request=ChangeEmailRequestSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "new_email": "jane.new@example.com",
                    "password": "Rw4$eTn8Kp2q",
                },
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=_DETAIL_SCHEMA,
                description="Confirmation link sent to the new address.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "detail": (
                                "A confirmation link has been sent to your "
                                "new email address."
                            )
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                description="The password is wrong, or the new email is already taken.",
                examples=[
                    OpenApiExample(
                        name="Wrong password",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "Incorrect password.",
                                    "field_name": "password",
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Email already taken",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "A user with this email already exists.",
                                    "field_name": "new_email",
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
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

    @extend_schema(
        summary="Confirm an email change",
        description=(
            "Consumes the token from an email-change confirmation link and "
            "applies the pending new email to the account.\n\n"
            "Called when the user opens the confirmation link emailed to "
            "their new address by "
            "`/api/v1/auth/change-email/`.\n\n"
            "**Auth:** Public - the token is the credential. The caller may "
            "not be signed in at all, since the link is opened from the "
            "new inbox rather than an active browser session; identity was "
            "already proven at the request step via the account's current "
            "password.\n\n"
            "**Prerequisites:** An email-change request must have been "
            "issued and the token not yet used or expired.\n\n"
            "**Important:** The token is single-use; replaying this call "
            "fails. This is the only step that actually changes "
            "`User.email` - after this, the *old* email can no longer be "
            "used to log in."
        ),
        tags=["Auth — Email Change"],
        request=ChangeEmailConfirmSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={"token": "8Kj2mNqR7vXyB4dW1sHfL6pT0aZcE3gU9nY5bV8rQmI"},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=_DETAIL_SCHEMA,
                description="Email address changed.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={"detail": "Email address changed successfully."},
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = ChangeEmailConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        auth_service.confirm_email_change(**serializer.validated_data)
        return Response({"detail": "Email address changed successfully."}, status=200)
