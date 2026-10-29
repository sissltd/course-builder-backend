from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.authentication.services.authentication_service import AuthenticationService
from api.onboarding.serializers import (
    CreatorProfileSerializer,
    OnboardingUpdateSerializer,
)
from api.onboarding.services import creator_profile_service

auth_service = AuthenticationService()


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
        summary="Retrieve my onboarding profile",
        tags=["Creator — Onboarding"],
        responses={200: OpenApiResponse(response=CreatorProfileSerializer)},
    )
    def get(self, request):
        profile = creator_profile_service.get_or_create_profile(user=request.user)
        return Response(CreatorProfileSerializer(profile).data)

    @extend_schema(
        summary="Update my onboarding profile",
        description=(
            "Partially updates the current user's creator profile - one "
            "wizard step at a time. Only supplied fields are touched, so a "
            "dropped-off wizard can resume freely.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Setting `agreement_accepted` to true returns a "
            "fresh access token in the response body."
        ),
        tags=["Creator — Onboarding"],
        request=OnboardingUpdateSerializer,
    )
    def patch(self, request):
        serializer = OnboardingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = creator_profile_service.update_profile(
            user=request.user, **serializer.validated_data
        )
        data = CreatorProfileSerializer(profile).data
        if serializer.validated_data.get("agreement_accepted"):
            data = {**data, **auth_service.generate_access_token(user=request.user)}
        return Response(data)
