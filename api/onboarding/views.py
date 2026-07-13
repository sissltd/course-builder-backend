from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.onboarding.serializers import CreatorProfileSerializer, OnboardingUpdateSerializer
from api.onboarding.services import creator_profile_service


class OnboardingView(APIView):
    """GET/PATCH the current user's onboarding profile.

    A single partial-update resource rather than one endpoint per wizard
    step: the frontend calls PATCH once per step with just that step's
    field(s), and creator_profile_service.update_profile only touches
    fields actually provided, so a dropped-off wizard can resume freely.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = OnboardingUpdateSerializer  # for schema generation only; not a GenericAPIView

    def get(self, request):
        profile = creator_profile_service.get_or_create_profile(user=request.user)
        return Response(CreatorProfileSerializer(profile).data)

    def patch(self, request):
        serializer = OnboardingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = creator_profile_service.update_profile(user=request.user, **serializer.validated_data)
        return Response(CreatorProfileSerializer(profile).data)
