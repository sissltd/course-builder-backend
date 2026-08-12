from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.authentication.services.authentication_service import AuthenticationService
from api.onboarding.serializers import (
    CreatorProfileSerializer,
    OnboardingUpdateSerializer,
)
from api.onboarding.services import creator_profile_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

auth_service = AuthenticationService()

_PROFILE_EXAMPLE = {
    "id": "3c7e5f20-91ab-4d63-8e5c-2f4a1b9d7e08",
    "primary_expertise_category": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
    "primary_expertise_area": "WEB_DEVELOPMENT",
    "primary_expertise_other": "",
    "video_comfort_level": "SOMEWHAT_COMFORTABLE",
    "monthly_course_capacity": "TWO_TO_THREE",
    "agreement_accepted_at": "2026-07-20T11:05:00.000Z",
    "agreement_accepted_version": "1.0",
    "onboarding_completed_at": "2026-07-20T11:05:00.000Z",
    "has_completed_onboarding": True,
    "needs_policy_reacceptance": False,
}

_EMPTY_PROFILE_EXAMPLE = {
    "id": "3c7e5f20-91ab-4d63-8e5c-2f4a1b9d7e08",
    "primary_expertise_category": None,
    "primary_expertise_area": "",
    "primary_expertise_other": "",
    "video_comfort_level": "",
    "monthly_course_capacity": "",
    "agreement_accepted_at": None,
    "agreement_accepted_version": None,
    "onboarding_completed_at": None,
    "has_completed_onboarding": False,
    "needs_policy_reacceptance": False,
}

_TOKEN_PAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "access": {"type": "string"},
        "refresh": {"type": "string"},
    },
}


class OnboardingView(APIView):
    """GET/PATCH the current user's onboarding profile.

    A single partial-update resource rather than one endpoint per wizard
    step: the frontend calls PATCH once per step with just that step's
    field(s), and creator_profile_service.update_profile only touches
    fields actually provided, so a dropped-off wizard can resume freely.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = (
        OnboardingUpdateSerializer  # for schema generation only; not a GenericAPIView
    )

    @extend_schema(
        summary="Retrieve the current user's onboarding profile",
        description=(
            "Returns the caller's onboarding profile, lazily creating an "
            "empty one on first access - there is no separate 'start "
            "onboarding' call.\n\n"
            "Called when the onboarding wizard loads, to resume at whatever "
            "step the creator left off on, and by any screen that needs to "
            "check `has_completed_onboarding` or "
            "`needs_policy_reacceptance` before allowing an action.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None beyond being signed in.\n\n"
            "**Important:** `needs_policy_reacceptance` is only ever `true` "
            "for a creator who already completed onboarding once, whose "
            "`agreement_accepted_version` no longer matches the platform's "
            "current `creator_agreement_policy_version` - it does not mean "
            "they haven't started onboarding yet. Course creation "
            "(`POST /api/v1/courses/`) is blocked while either "
            "`has_completed_onboarding` is `false` or "
            "`needs_policy_reacceptance` is `true`."
        ),
        tags=["Auth — Onboarding"],
        responses={
            200: OpenApiResponse(
                response=CreatorProfileSerializer,
                description="The caller's onboarding profile.",
                examples=[
                    OpenApiExample(name="Not started", value=_EMPTY_PROFILE_EXAMPLE),
                    OpenApiExample(name="Completed", value=_PROFILE_EXAMPLE),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        profile = creator_profile_service.get_or_create_profile(user=request.user)
        return Response(CreatorProfileSerializer(profile).data)

    @extend_schema(
        summary="Update the current user's onboarding profile",
        description=(
            "Applies whichever onboarding-wizard field(s) are supplied. "
            "Only the fields actually sent are touched, so the frontend "
            "calls this once per wizard step with a partial payload and a "
            "creator can resume a dropped-off wizard without losing earlier "
            "steps.\n\n"
            "Called from each step of the onboarding wizard as the creator "
            "advances, and again later if they need to re-accept an updated "
            "creator agreement.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** At least one field must be provided. "
            "`other_expertise` is required if `expertise_area` is "
            "`'OTHERS'`.\n\n"
            "**Important:** Sending `agreement_accepted: true` for the "
            "**first** time stamps `onboarding_completed_at`, unlocks "
            "Course Builder access, and the response includes a fresh "
            "`access`/`refresh` token pair (mirroring signup/staff-"
            "acceptance) so the frontend can land the creator straight in "
            "the dashboard. Sending it **again** later (e.g. because "
            "`needs_policy_reacceptance` was `true`) re-accepts at "
            "whatever policy version is currently in effect and does "
            "**not** re-issue tokens, reset `onboarding_completed_at`, or "
            "repeat the completion notification - only the plain updated "
            "profile is returned."
        ),
        tags=["Auth — Onboarding"],
        request=OnboardingUpdateSerializer,
        examples=[
            OpenApiExample(
                name="Step 1: expertise",
                request_only=True,
                value={
                    "category_id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
                    "expertise_area": "WEB_DEVELOPMENT",
                },
            ),
            OpenApiExample(
                name="Step 2: video comfort",
                request_only=True,
                value={"video_comfort_level": "SOMEWHAT_COMFORTABLE"},
            ),
            OpenApiExample(
                name="Step 3: capacity",
                request_only=True,
                value={"monthly_course_capacity": "TWO_TO_THREE"},
            ),
            OpenApiExample(
                name="Step 4: accept agreement (first time or re-acceptance)",
                request_only=True,
                value={"agreement_accepted": True},
            ),
        ],
        responses={
            200: OpenApiResponse(
                description=(
                    "Profile updated. Includes `access`/`refresh` only on "
                    "first-time completion."
                ),
                response={
                    "oneOf": [
                        {"$ref": "#/components/schemas/CreatorProfile"},
                        {
                            "allOf": [
                                {"$ref": "#/components/schemas/CreatorProfile"},
                                _TOKEN_PAIR_SCHEMA,
                            ]
                        },
                    ]
                },
                examples=[
                    OpenApiExample(name="Step update", value=_EMPTY_PROFILE_EXAMPLE),
                    OpenApiExample(
                        name="First-time completion (issues tokens)",
                        value={
                            **_PROFILE_EXAMPLE,
                            "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIn0.7Qm2",
                            "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCJ9.9Lp4",
                        },
                    ),
                ],
            ),
            400: OpenApiResponse(
                description=(
                    "No field was provided, or other_expertise is missing "
                    "while expertise_area is 'OTHERS'."
                ),
                examples=[
                    OpenApiExample(
                        name="No fields provided",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "At least one onboarding field must "
                                        "be provided."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Missing other_expertise",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "other_expertise is required when "
                                        "expertise_area is 'Others'."
                                    ),
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
    def patch(self, request):
        serializer = OnboardingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = creator_profile_service.update_profile(
            user=request.user, request=request, **serializer.validated_data
        )
        data = CreatorProfileSerializer(profile).data
        if profile.is_first_completion:
            data = {**data, **auth_service.generate_access_token(user=request.user)}
        return Response(data)
