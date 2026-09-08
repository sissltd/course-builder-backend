from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.authentication.serializers import GoogleLoginSerializer, GoogleSignupSerializer
from api.authentication.services import google_auth_service
from api.users.enums import UserRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_GOOGLE_AUTH_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["access", "refresh", "user", "role", "workspace"],
    "properties": {
        "access": {"type": "string"},
        "refresh": {"type": "string"},
        "user": {"type": "object"},
        "role": {"type": "string"},
        "workspace": {"type": "string"},
    },
}

_GOOGLE_AUTH_SUCCESS_EXAMPLE = {
    "access": "eyJhbGciOiJIUzI1NiJ9.creator-access-token",
    "refresh": "eyJhbGciOiJIUzI1NiJ9.creator-refresh-token",
    "user": {
        "id": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
        "email": "jane.doe@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "country": "NG",
        "role": "COURSE_CREATOR",
        "is_active": True,
        "status": "ACTIVE",
        "has_completed_onboarding": False,
    },
    "role": "COURSE_CREATOR",
    "workspace": "creator_studio",
}

_GOOGLE_VALIDATION_RESPONSE = OpenApiResponse(
    description=(
        "The Google credential is invalid, the account is not eligible, the "
        "account is locked/inactive, or a required signup field is invalid."
    ),
    examples=[
        OpenApiExample(
            name="Invalid Google credential",
            value={
                "errors": [
                    {
                        "type": "validation_error",
                        "code": "invalid",
                        "message": "Invalid Google credential.",
                        "field_name": "id_token",
                    }
                ]
            },
        )
    ],
)

_GOOGLE_UNAVAILABLE_RESPONSE = OpenApiResponse(
    description="Google authentication is not configured for this deployment.",
    examples=[
        OpenApiExample(
            name="Google authentication unavailable",
            value={
                "errors": [
                    {
                        "type": "server_error",
                        "code": "google_auth_unavailable",
                        "message": "Google authentication is temporarily unavailable.",
                        "field_name": None,
                    }
                ]
            },
        )
    ],
)


def _signup_schema(*, reviewer: bool):
    role_label = "Creator Reviewer" if reviewer else "Course Creator"
    role_value = "CREATOR_REVIEWER" if reviewer else "COURSE_CREATOR"
    workspace = "creator_review_dashboard" if reviewer else "creator_studio"
    endpoint = (
        "/api/v1/auth/reviewer/signup/google/"
        if reviewer
        else "/api/v1/auth/signup/google/"
    )
    return extend_schema(
        summary=f"Sign up a {role_label} with Google",
        description=(
            f"Verifies a Google ID token and creates an active `{role_value}` "
            "account without a password or a separate email-verification step. "
            "It immediately returns the platform's normal JWT/session payload.\n\n"
            f"Called from the Google button on the {role_label} account-creation "
            f"screen at `{endpoint}`.\n\n"
            "**Auth:** Public — the Google ID token is the credential.\n\n"
            "**Prerequisites:** The frontend must obtain an ID token from a "
            "Google client ID configured in `GOOGLE_OAUTH_CLIENT_IDS`; the token "
            "must contain a Google-verified email.\n\n"
            f"**Important:** New accounts are forced to `{role_value}` regardless "
            "of client input. If the verified email already belongs to an eligible "
            "Course Creator or Creator Reviewer, Google is linked and the existing "
            "role/profile is preserved. A new account returns 201; an existing "
            "account returns 200. Terms must be accepted."
        ),
        tags=["Auth — Signup & Verification"],
        request=GoogleSignupSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "id_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6IjE2NzAyNyJ9...",
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "country": "NG",
                    "terms_accepted": True,
                },
            )
        ],
        responses={
            200: OpenApiResponse(
                response=_GOOGLE_AUTH_RESPONSE_SCHEMA,
                description="Existing eligible account linked and signed in.",
                examples=[
                    OpenApiExample(
                        name="Existing account",
                        value={
                            **_GOOGLE_AUTH_SUCCESS_EXAMPLE,
                            "role": role_value,
                            "workspace": workspace,
                        },
                    )
                ],
            ),
            201: OpenApiResponse(
                response=_GOOGLE_AUTH_RESPONSE_SCHEMA,
                description=f"Active {role_label} account created and signed in.",
                examples=[
                    OpenApiExample(
                        name="New account",
                        value={
                            **_GOOGLE_AUTH_SUCCESS_EXAMPLE,
                            "user": {
                                **_GOOGLE_AUTH_SUCCESS_EXAMPLE["user"],
                                "role": role_value,
                            },
                            "role": role_value,
                            "workspace": workspace,
                        },
                    )
                ],
            ),
            400: _GOOGLE_VALIDATION_RESPONSE,
            503: _GOOGLE_UNAVAILABLE_RESPONSE,
            **STANDARD_ERROR_RESPONSES["rate_limited"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )


@extend_schema(auth=[{}])
class GoogleSignupView(APIView):
    """Create or link a Course Creator account using a Google ID token."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "signup"
    serializer_class = GoogleSignupSerializer
    signup_role = UserRole.COURSE_CREATOR

    @_signup_schema(reviewer=False)
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data
        data, created = google_auth_service.signup_with_google(
            raw_token=validated_data["id_token"],
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            country=validated_data["country"],
            terms_accepted=validated_data["terms_accepted"],
            role=self.signup_role,
            request=request,
        )
        return Response(data, status=201 if created else 200)


@extend_schema(auth=[{}])
class GoogleReviewerSignupView(GoogleSignupView):
    """Create or link a Creator Reviewer account using a Google ID token."""

    signup_role = UserRole.CREATOR_REVIEWER

    @_signup_schema(reviewer=True)
    def post(self, request):
        return super().post(request)


@extend_schema(auth=[{}])
class GoogleLoginView(APIView):
    """Sign an existing Course Creator or Creator Reviewer in with Google."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    serializer_class = GoogleLoginSerializer

    @extend_schema(
        summary="Log in with Google",
        description=(
            "Verifies a Google ID token, links it to an eligible account when "
            "the verified email matches, and returns the platform's normal JWT, "
            "session, role, user, and workspace payload. It never creates a new "
            "local account.\n\n"
            "Called from the Google button on either the Course Creator or "
            "Creator Reviewer login screen.\n\n"
            "**Auth:** Public — the Google ID token is the credential.\n\n"
            "**Prerequisites:** A Course Creator or Creator Reviewer account must "
            "already exist, and the frontend must obtain the token using a client "
            "ID configured in `GOOGLE_OAUTH_CLIENT_IDS`.\n\n"
            "**Important:** Account role is read from the existing account, not "
            "from the URL or request. Unknown accounts must use Google signup. "
            "Suspended, deactivated, and currently locked accounts are refused."
        ),
        tags=["Auth — Session"],
        request=GoogleLoginSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={"id_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6IjE2NzAyNyJ9..."},
            )
        ],
        responses={
            200: OpenApiResponse(
                response=_GOOGLE_AUTH_RESPONSE_SCHEMA,
                description="Existing eligible account signed in.",
                examples=[
                    OpenApiExample(name="Success", value=_GOOGLE_AUTH_SUCCESS_EXAMPLE)
                ],
            ),
            400: _GOOGLE_VALIDATION_RESPONSE,
            503: _GOOGLE_UNAVAILABLE_RESPONSE,
            **STANDARD_ERROR_RESPONSES["rate_limited"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = google_auth_service.login_with_google(
            raw_token=serializer.validated_data["id_token"],
            request=request,
        )
        return Response(data, status=200)
