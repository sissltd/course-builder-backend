from drf_spectacular.utils import OpenApiResponse, extend_schema
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


class MeView(RetrieveUpdateAPIView):
    """Retrieve or partially update the current authenticated user's profile.

    Email is deliberately not editable here - see MeUpdateSerializer.
    """

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
        tags=["Users — Reviewer Preferences"],
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
        tags=["Users — Reviewer Preferences"],
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
        tags=["Users — Reviewer Preferences"],
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
        tags=["Users — Reviewer Preferences"],
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
