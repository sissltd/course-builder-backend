from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import filters as drf_filters
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.views import APIView

from api.authentication.services import activity_service
from api.users.enums import (
    KYCStatus,
    UserActivityActionEnums,
    UserActivityCategoryEnums,
)
from api.users.filters import KYCReviewQueueFilter
from api.users.models import KYCVerification
from api.users.permissions import IsAdminOrSuperAdminRole
from api.users.serializers import (
    KYCReviewApproveSerializer,
    KYCReviewRejectSerializer,
    KYCVerificationAdminSerializer,
    KYCVerificationSerializer,
    KYCVerificationSubmitSerializer,
)
from api.users.services import kyc_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES


class KYCVerificationView(APIView):
    """GET the current user's latest KYC submission, or POST a new one.

    Any authenticated user can call this (not role-gated) - KYC is an
    identity concern independent of platform role.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = KYCVerificationSubmitSerializer  # for schema generation only

    @extend_schema(
        summary="Get latest KYC submission",
        description=(
            "Returns the current user's most recent KYC submission, or "
            "`null` if none has been made yet.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None."
        ),
        tags=["Users — KYC"],
        responses={
            200: OpenApiResponse(response=KYCVerificationSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        latest = kyc_service.get_latest_verification(user=request.user)
        if latest is None:
            return Response(None)
        return Response(KYCVerificationSerializer(latest).data)

    @extend_schema(
        summary="Submit KYC verification",
        description=(
            "Submits identity documents for KYC verification, starting a "
            "new Pending review.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None - not role-gated."
        ),
        tags=["Users — KYC"],
        request=KYCVerificationSubmitSerializer,
        responses={
            201: OpenApiResponse(response=KYCVerificationSerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
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


@extend_schema_view(
    list=extend_schema(
        summary="List KYC review queue",
        description=(
            "Lists KYC submissions for admin review. Defaults to PENDING "
            "submissions (the actual queue), narrowable via the `status` "
            "query parameter.\n\n"
            "**Auth:** Admin or Super Admin.\n\n"
            "**Prerequisites:** None."
        ),
        tags=["Users — KYC Review"],
        responses={
            200: OpenApiResponse(response=KYCVerificationAdminSerializer(many=True)),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a KYC submission",
        description=(
            "Returns a single KYC submission, including the submitting "
            "user and raw `id_number`.\n\n"
            "**Auth:** Admin or Super Admin.\n\n"
            "**Prerequisites:** The submission must exist."
        ),
        tags=["Users — KYC Review"],
        responses={
            200: OpenApiResponse(response=KYCVerificationAdminSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class KYCReviewViewSet(ReadOnlyModelViewSet):
    """Admin review queue for KYC verification submissions.

    Restricted to Admins and Super Admins (not invited Approvers - see
    IsAdminOrSuperAdminRole). `list` defaults to PENDING submissions (the
    actual queue), narrowable via KYCReviewQueueFilter's ?status= param;
    detail actions look up any submission by id, so acting on one in the
    wrong status produces a 400 from the service layer rather than a
    misleading 404. Mirrors CourseReviewViewSet's shape.
    """

    permission_classes = [IsAdminOrSuperAdminRole]
    filterset_class = KYCReviewQueueFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["created_datetime"]
    ordering = ["created_datetime"]

    def get_queryset(self):
        queryset = KYCVerification.objects.select_related("user", "reviewed_by")
        if self.action == "list" and "status" not in self.request.query_params:
            return queryset.filter(status=KYCStatus.PENDING)
        return queryset

    def get_serializer_class(self):
        if self.action == "approve":
            return KYCReviewApproveSerializer
        if self.action == "reject":
            return KYCReviewRejectSerializer
        return KYCVerificationAdminSerializer

    @extend_schema(
        summary="Approve a KYC submission",
        description=(
            "Approves a KYC submission. Takes no body - approval needs no "
            "accompanying data.\n\n"
            "**Auth:** Admin or Super Admin.\n\n"
            "**Prerequisites:** The submission must exist."
        ),
        tags=["Users — KYC Review"],
        request=KYCReviewApproveSerializer,
        responses={
            200: OpenApiResponse(response=KYCVerificationAdminSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        verification = kyc_service.approve_verification(
            verification=self.get_object(), reviewer=request.user
        )
        return Response(KYCVerificationAdminSerializer(verification).data)

    @extend_schema(
        summary="Reject a KYC submission",
        description=(
            "Rejects a KYC submission with a required "
            "`rejection_reason` the submitter can act on when "
            "resubmitting.\n\n"
            "**Auth:** Admin or Super Admin.\n\n"
            "**Prerequisites:** The submission must exist."
        ),
        tags=["Users — KYC Review"],
        request=KYCReviewRejectSerializer,
        responses={
            200: OpenApiResponse(response=KYCVerificationAdminSerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        serializer = KYCReviewRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        verification = kyc_service.reject_verification(
            verification=self.get_object(),
            reviewer=request.user,
            rejection_reason=serializer.validated_data["rejection_reason"],
        )
        return Response(KYCVerificationAdminSerializer(verification).data)
