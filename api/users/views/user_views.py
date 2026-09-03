from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.authentication.services import activity_service
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.serializers import (
    MeSerializer,
    MeUpdateSerializer,
    QueueBehaviourPreferenceSerializer,
    QueueBehaviourPreferenceUpdateSerializer,
    ReviewerAvailabilitySerializer,
    ReviewerAvailabilityUpdateSerializer,
)
from api.users.services import queue_preference_service, reviewer_availability_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_ME_EXAMPLE = {
    "id": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    "email": "jane.doe@example.com",
    "first_name": "Jane",
    "last_name": "Doe",
    "full_name": "Jane Doe",
    "country": "NG",
    "state": "Lagos",
    "address": "14 Admiralty Way, Lekki Phase 1",
    "phone_number": "+2348012345678",
    "timezone": "Africa/Lagos",
    "avatar_url": "",
    "terms_accepted_at": "2026-07-12T09:30:11.204Z",
    "role": "COURSE_CREATOR",
    "is_active": False,
    "status": "PENDING",
    "created_datetime": "2026-07-12T09:30:11.204Z",
    "updated_datetime": "2026-07-12T09:30:11.204Z",
    "member_since": "2026-07-12T09:30:11.204Z",
    "has_completed_onboarding": False,
    "is_verified": False,
    "badges": [],
    "category": {
        "id": "0bd326eb-e48e-44bc-b963-2c8945210c2d",
        "name": "Web Applications",
    },
}


@extend_schema_view(
    get=extend_schema(
        summary="Retrieve my profile",
        description=(
            "Returns the signed-in creator's Figma-ready profile, including "
            "name, avatar, membership date, KYC verification state, badges, "
            "contact details, address, expertise category, and account "
            "metadata. The frontend uses this "
            "endpoint to populate account settings and keep the profile "
            "form in sync with server state.\n\n"
            "Called when the profile screen opens and whenever the frontend "
            "needs to refresh cached account details after an edit.\n\n"
            "**Auth:** Authenticated Course Creator.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** `is_verified` represents approved KYC status. "
            "`badges` is an empty list until a badge-award domain is added."
        ),
        tags=["Creator — Profile"],
        responses={
            200: OpenApiResponse(
                response=MeSerializer,
                description="The current user profile.",
                examples=[OpenApiExample(name="Success", value=_ME_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    patch=extend_schema(
        summary="Update my profile",
        description=(
            "Partially updates the signed-in user's profile. Only the fields "
            "present in the request body are changed, so the frontend can "
            "save one section of the form without resending the entire "
            "resource.\n\n"
            "Called from the profile edit screen after the user saves "
            "personal details such as name, timezone, address, or category.\n\n"
            "**Auth:** Authenticated Course Creator.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Email is intentionally not editable here. "
            "`category` only accepts an active category id; invalid or "
            "inactive ids are rejected by validation. The `category` set here "
            "is used to update the CreatorProfile's category field."
        ),
        tags=["Creator — Profile"],
        request=MeUpdateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "first_name": "Janet",
                    "last_name": "Adebayo",
                    "timezone": "Africa/Lagos",
                    "avatar_url": "https://cdn.example.com/avatars/janet.jpg",
                    "phone_number": "+2348012345678",
                    "country": "NG",
                    "state": "Lagos",
                    "address": "14 Admiralty Way, Lekki Phase 1",
                    "category": "2e9c4a71-58b3-4d06-9f27-6a1e8c0b5d34",
                },
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=MeSerializer,
                description="The updated user profile.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            **_ME_EXAMPLE,
                            "first_name": "Janet",
                            "last_name": "Adebayo",
                            "timezone": "Africa/Lagos",
                            "country": "NG",
                            "state": "Lagos",
                            "address": "14 Admiralty Way, Lekki Phase 1",
                            "phone_number": "+2348012345678",
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class MeView(RetrieveUpdateAPIView):
    """Retrieve or partially update the current authenticated user's profile.

    Email is deliberately not editable here - see MeUpdateSerializer.
    """

    # Self-scoped: get_object() returns request.user, so no role can reach
    # another account's record. Every role needs its own Account settings
    # screen - reviewers included - so this is deliberately not gated on
    # IsPublicCourseCreatorRole, which would 403 reviewers and staff.
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return MeUpdateSerializer
        return MeSerializer

    def perform_update(self, serializer):
        serializer.save()
        activity_service.log_activity(
            user=self.request.user,
            category=UserActivityCategoryEnums.CONFIGURATION,
            action=UserActivityActionEnums.PROFILE_UPDATED,
            summary="Updated profile settings.",
            request=self.request,
        )


class ReviewerAvailabilityView(APIView):
    """GET/PATCH the current user's reviewer-availability settings.

    Lazily creates the row on first GET, same pattern as onboarding's
    OnboardingView. Any authenticated user can technically call this (not
    role-gated), since it's harmless for a non-reviewer to have an unused row.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = (
        ReviewerAvailabilityUpdateSerializer  # for schema generation only
    )

    @extend_schema(
        summary="Get reviewer availability",
        description=(
            "Returns the current user's reviewer-availability settings, "
            "creating a default row on first call.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None."
        ),
        tags=["Reviewer — Availability"],
        responses={
            200: OpenApiResponse(response=ReviewerAvailabilitySerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        availability = reviewer_availability_service.get_or_create_availability(
            user=request.user
        )
        return Response(ReviewerAvailabilitySerializer(availability).data)

    @extend_schema(
        summary="Update reviewer availability",
        description=(
            "Updates one or more of the current user's reviewer-"
            "availability settings. All fields are optional.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None."
        ),
        tags=["Reviewer — Availability"],
        request=ReviewerAvailabilityUpdateSerializer,
        responses={
            200: OpenApiResponse(response=ReviewerAvailabilitySerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def patch(self, request):
        serializer = ReviewerAvailabilityUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        availability = reviewer_availability_service.update_availability(
            user=request.user, **serializer.validated_data
        )
        activity_service.log_activity(
            user=request.user,
            category=UserActivityCategoryEnums.CONFIGURATION,
            action=UserActivityActionEnums.AVAILABILITY_UPDATED,
            summary="Updated availability settings.",
            request=request,
        )
        return Response(ReviewerAvailabilitySerializer(availability).data)


class QueueBehaviourPreferenceView(APIView):
    """GET/PATCH the current user's review-queue behaviour preferences.

    Lazily creates the row on first GET, same pattern as
    ReviewerAvailabilityView. Not role-gated, same rationale.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = (
        QueueBehaviourPreferenceUpdateSerializer  # for schema generation only
    )

    @extend_schema(
        summary="Get queue behaviour preferences",
        description=(
            "Returns the current user's review-queue behaviour "
            "preferences, creating a default row on first call.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None."
        ),
        tags=["Reviewer — Queue Preferences"],
        responses={
            200: OpenApiResponse(response=QueueBehaviourPreferenceSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        preference = queue_preference_service.get_or_create_preference(
            user=request.user
        )
        return Response(QueueBehaviourPreferenceSerializer(preference).data)

    @extend_schema(
        summary="Update queue behaviour preferences",
        description=(
            "Updates one or more of the current user's review-queue "
            "behaviour preferences. All fields are optional.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None."
        ),
        tags=["Reviewer — Queue Preferences"],
        request=QueueBehaviourPreferenceUpdateSerializer,
        responses={
            200: OpenApiResponse(response=QueueBehaviourPreferenceSerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def patch(self, request):
        serializer = QueueBehaviourPreferenceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        preference = queue_preference_service.update_preference(
            user=request.user, **serializer.validated_data
        )
        activity_service.log_activity(
            user=request.user,
            category=UserActivityCategoryEnums.CONFIGURATION,
            action=UserActivityActionEnums.QUEUE_PREFERENCES_UPDATED,
            summary="Updated queue behaviour settings.",
            request=request,
        )
        return Response(QueueBehaviourPreferenceSerializer(preference).data)
