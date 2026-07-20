from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.authentication.services import activity_service
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.serializers import (
    MeSerializer,
    MeUpdateSerializer,
    ReviewerAvailabilitySerializer,
    ReviewerAvailabilityUpdateSerializer,
)
from api.users.services import reviewer_availability_service


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

    def get(self, request):
        availability = reviewer_availability_service.get_or_create_availability(
            user=request.user
        )
        return Response(ReviewerAvailabilitySerializer(availability).data)

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
