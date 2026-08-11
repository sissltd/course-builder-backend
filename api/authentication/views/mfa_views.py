from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import exceptions
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.authentication.serializers import (
    MFACodeSerializer,
    MFAEnrollResponseSerializer,
    MFARecoveryCodesResponseSerializer,
    MFAVerifySerializer,
)
from api.authentication.services import authentication_service, mfa_service
from api.users.models import User
from api.users.permissions import IsSuperAdminRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_ENROLL_EXAMPLE = {
    "secret": "JBSWY3DPEHPK3PXP",
    "otpauth_uri": "otpauth://totp/TSES:jane.doe@example.com?secret=JBSWY3DPEHPK3PXP&issuer=TSES",
    "qr_code_base64": "iVBORw0KGgoAAAANSUhEUgAA...",
}

_RECOVERY_CODES_EXAMPLE = {
    "recovery_codes": ["4f9c2a1b", "8d3e7f0a", "1c6b9d2e", "7a4f0c3b"],
}

_DETAIL_SCHEMA = {"type": "object", "properties": {"detail": {"type": "string"}}}

_LOGIN_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "access": {"type": "string"},
        "refresh": {"type": "string"},
        "user": {"type": "object"},
        "role": {"type": "string"},
        "workspace": {"type": "string"},
    },
}


class MFAEnrollView(APIView):
    """Start (or restart) TOTP enrollment for the current user."""

    permission_classes = [IsAuthenticated]
    serializer_class = MFAEnrollResponseSerializer

    @extend_schema(
        summary="Start TOTP enrollment",
        description=(
            "Generates a new TOTP secret and QR code for the current user, "
            "to be scanned into an authenticator app. Calling this again "
            "before confirming replaces the previous, unconfirmed secret.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** The device is not enabled until "
            "`POST /api/v1/auth/mfa/enroll/confirm/` succeeds with a live "
            "code from the returned secret."
        ),
        tags=["Auth — MFA"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=MFAEnrollResponseSerializer,
                description="A fresh, unconfirmed TOTP secret and QR code.",
                examples=[OpenApiExample(name="Success", value=_ENROLL_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        result = mfa_service.enroll(user=request.user)
        return Response(MFAEnrollResponseSerializer(result).data)


class MFAEnrollConfirmView(APIView):
    """Confirm enrollment with a live code; enables the device and returns
    a fresh batch of recovery codes (shown once)."""

    permission_classes = [IsAuthenticated]
    serializer_class = MFACodeSerializer

    @extend_schema(
        summary="Confirm TOTP enrollment",
        description=(
            "Confirms a pending TOTP enrollment with a live code, enabling "
            "the device and issuing a fresh batch of recovery codes.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** `POST /api/v1/auth/mfa/enroll/` must have "
            "been called first.\n\n"
            "**Important:** `recovery_codes` are shown once in this "
            "response only - store them; they cannot be retrieved again "
            "later, only regenerated (which invalidates the old batch)."
        ),
        tags=["Auth — MFA"],
        request=MFACodeSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request", request_only=True, value={"code": "123456"}
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=MFARecoveryCodesResponseSerializer,
                description="Device enabled; fresh recovery codes issued.",
                examples=[
                    OpenApiExample(name="Success", value=_RECOVERY_CODES_EXAMPLE)
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = MFACodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        codes = mfa_service.confirm_enrollment(
            user=request.user,
            code=serializer.validated_data["code"],
            request=request,
        )
        return Response(
            MFARecoveryCodesResponseSerializer({"recovery_codes": codes}).data
        )


class MFAVerifyView(APIView):
    """Second half of login: exchange a challenge_token + live code for the
    normal login token payload."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    serializer_class = MFAVerifySerializer

    @extend_schema(
        summary="Verify MFA challenge",
        description=(
            "Second half of login for a user whose account has MFA "
            "enabled: exchanges the `challenge_token` from the initial "
            "login call plus a live TOTP (or recovery) code for the normal "
            "JWT login token payload.\n\n"
            "**Auth:** Public - `challenge_token` itself is the "
            "credential.\n\n"
            "**Prerequisites:** `POST /api/v1/auth/login/` must have "
            "returned a `challenge_token` (i.e. the account has MFA "
            "enabled).\n\n"
            "**Important:** The response shape is identical to a normal "
            "login response, not `MFAVerifySerializer` (which only "
            "describes the request)."
        ),
        tags=["Auth — MFA"],
        request=MFAVerifySerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={"challenge_token": "8f3c2a1b9d0e4f7a", "code": "123456"},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=_LOGIN_RESPONSE_SCHEMA,
                description="Logged in.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIn0.7Qm2",
                            "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCJ9.9Lp4",
                            "user": {
                                "id": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
                                "email": "admin@example.com",
                                "role": "ADMIN",
                            },
                            "role": "ADMIN",
                            "workspace": "admin_dashboard",
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = MFAVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = mfa_service.verify_challenge(
            challenge_token=serializer.validated_data["challenge_token"],
            code=serializer.validated_data["code"],
            request=request,
        )
        data = authentication_service.finish_login(
            user=user, request=request, mfa_verified=True
        )
        return Response(data)


class MFARecoveryCodesRegenerateView(APIView):
    """Requires a fresh valid TOTP code; burns the old recovery codes and
    issues a new batch (shown once)."""

    permission_classes = [IsAuthenticated]
    serializer_class = MFACodeSerializer

    @extend_schema(
        summary="Regenerate recovery codes",
        description=(
            "Burns the current user's existing recovery codes and issues a "
            "fresh batch, after confirming a live TOTP code.\n\n"
            "**Auth:** Any authenticated user with MFA enabled.\n\n"
            "**Prerequisites:** The user must have an enabled MFA "
            "device.\n\n"
            "**Important:** The old batch stops working immediately - only "
            "the codes in this response are valid afterward."
        ),
        tags=["Auth — MFA"],
        request=MFACodeSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request", request_only=True, value={"code": "123456"}
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=MFARecoveryCodesResponseSerializer,
                description="A fresh batch of recovery codes.",
                examples=[
                    OpenApiExample(name="Success", value=_RECOVERY_CODES_EXAMPLE)
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = MFACodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        codes = mfa_service.regenerate_recovery_codes(
            user=request.user,
            code=serializer.validated_data["code"],
            request=request,
        )
        return Response(
            MFARecoveryCodesResponseSerializer({"recovery_codes": codes}).data
        )


class MFADisableView(APIView):
    """Self-service disable - 403 for ADMIN/SUPER_ADMIN, since MFA is
    mandatory for those roles (see mfa_service.disable)."""

    permission_classes = [IsAuthenticated]
    serializer_class = MFACodeSerializer

    @extend_schema(
        summary="Disable MFA",
        description=(
            "Disables MFA for the current user after confirming a live "
            "TOTP code, deleting the device and its recovery codes.\n\n"
            "**Auth:** Any authenticated user, except Admin/Super Admin - "
            "MFA is mandatory for those roles.\n\n"
            "**Prerequisites:** The user must have an enabled MFA "
            "device.\n\n"
            "**Important:** Disabling means the account no longer prompts "
            "for a second factor at login - re-enrollment starts from "
            "scratch (a new secret, new recovery codes)."
        ),
        tags=["Auth — MFA"],
        request=MFACodeSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request", request_only=True, value={"code": "123456"}
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=_DETAIL_SCHEMA,
                description="MFA disabled.",
                examples=[
                    OpenApiExample(name="Success", value={"detail": "MFA disabled."})
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = MFACodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mfa_service.disable(
            user=request.user,
            code=serializer.validated_data["code"],
            request=request,
        )
        return Response({"detail": "MFA disabled."})


class MFAAdminResetView(APIView):
    """Super-Admin-only: reset another user's MFA device (lost-device
    recovery). Forces re-enrollment; does not grant a fresh grace period."""

    permission_classes = [IsSuperAdminRole]

    @extend_schema(
        summary="Reset a user's MFA (admin)",
        description=(
            "Super-Admin-only lost-device recovery: deletes the target "
            "user's MFA device and recovery codes, forcing them to "
            "re-enroll from scratch on next login.\n\n"
            "**Auth:** Super Admin only.\n\n"
            "**Prerequisites:** `user_id` must belong to an existing "
            "user.\n\n"
            "**Important:** Does not grant a fresh MFA grace period - if "
            "the target's role requires MFA, they must re-enroll before "
            "resuming normal access."
        ),
        tags=["Auth — MFA"],
        request=None,
        parameters=[
            OpenApiParameter(
                name="user_id",
                type=str,
                location=OpenApiParameter.PATH,
                description="The target user's id.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=_DETAIL_SCHEMA,
                description="MFA reset for the target user.",
                examples=[
                    OpenApiExample(
                        name="Success", value={"detail": "MFA reset for user."}
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, user_id):
        try:
            target_user = User.objects.get(id=user_id)
        except User.DoesNotExist as exc:
            raise exceptions.NotFound("User not found.") from exc

        mfa_service.admin_reset(
            acting_admin=request.user, target_user=target_user, request=request
        )
        return Response({"detail": "MFA reset for user."})
