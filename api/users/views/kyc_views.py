from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.authentication.services import activity_service
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.serializers import (
    KYCVerificationSerializer,
    KYCVerificationSubmitSerializer,
)
from api.users.services import kyc_service


class KYCVerificationView(APIView):
    """GET the current user's latest KYC submission, or POST a new one.

    Any authenticated user can call this (not role-gated) - KYC is an
    identity concern independent of platform role.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = KYCVerificationSubmitSerializer  # for schema generation only

    def get(self, request):
        latest = kyc_service.get_latest_verification(user=request.user)
        if latest is None:
            return Response(None)
        return Response(KYCVerificationSerializer(latest).data)

    def post(self, request):
        serializer = KYCVerificationSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submission = kyc_service.submit_verification(
            user=request.user, **serializer.validated_data
        )
        activity_service.log_activity(
            user=request.user,
            category=UserActivityCategoryEnums.KYC,
            action=UserActivityActionEnums.KYC_SUBMITTED,
            summary="Submitted KYC verification documents.",
            request=request,
        )
        return Response(
            KYCVerificationSerializer(submission).data, status=status.HTTP_201_CREATED
        )
