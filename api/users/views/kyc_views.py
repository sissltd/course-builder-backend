from typing import Any, ClassVar, cast

from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import filters as drf_filters
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ReadOnlyModelViewSet

from api.authentication.services import activity_service
from api.users.enums import (
    KYCStatus,
    UserActivityActionEnums,
    UserActivityCategoryEnums,
)
from api.users.exceptions import SISSLError, SISSLLivenessFailed
from api.users.filters import KYCReviewQueueFilter
from api.users.models import KYCVerification, User
from api.users.permissions import IsAdminOrSuperAdminRole
from api.users.serializers import (
    KYCReviewApproveSerializer,
    KYCReviewRejectSerializer,
    KYCVerificationAdminSerializer,
    KYCVerificationSerializer,
    KYCVerificationSubmitSerializer,
)
from api.users.serializers.kyc_serializer import KYCReviewFlagSerializer, LivenessSerializer
from api.users.services.kyc_services import kyc_submission_service
from api.users.services.kyc_services.sissl_service import SISSLServices
from api.users.services.kyc_services.utils import persist_liveness_avatar
from includes.spectacular.responses import (
    STANDARD_ERROR_RESPONSES,
    ErrorEnvelopeSerializer,
    inline_success_response,
)
from shared.response.error import custom_error_response
from shared.response.success import custom_success_response
from shared.spectacular.responses import inline_error_response
from shared.utils.client_meta import client_meta


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
            "**Prerequisites:** None.\n\n"
            "**Important:** The `sissl_user_data` field is a read-only representation of the user data returned from SISSL, if any. The field values may be `null` if no data has been returned yet."
        ),
        tags=["Creator — KYC"],
        responses={
            200: OpenApiResponse(response=KYCVerificationSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        latest = kyc_submission_service.get_latest_verification(user=request.user)
        return custom_success_response(
            message="Retrieved successfully",
            data=KYCVerificationSerializer(latest).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary="Submit KYC verification",
        description=(
            "Submits identity documents for KYC verification, starting a "
            "new Pending review.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None - not role-gated."
        ),
        tags=["Creator — KYC"],
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
        submission = kyc_submission_service.submit_verification(
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
            "**Prerequisites:** Authenticated Admin or Super Admin.\n\n"
            "**Important:** The `user` field captures the submitted user information while `kyc_user_data` captures the data returned from the KYC provider. `kyc_request_status` reflects the status of the request with the KYC provider — `found` or `not found`"
        ),
        tags=["Admin — KYC Review"],
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
            "**Prerequisites:** Authenticated Admin or Super Admin.\n\n"
            "**Important:** The `user` field captures the submitted user information while `kyc_user_data` captures the data returned from the KYC provider. `kyc_request_status` reflects the status of the request with the KYC provider — `found` or `not found`"
        ),
        tags=["Admin — KYC Review"],
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
        if self.action == "flag":
            return KYCReviewFlagSerializer
        return KYCVerificationAdminSerializer

    @extend_schema(
        summary="Approve a KYC submission",
        description=(
            "Approves a KYC submission. Takes no body - approval needs no "
            "accompanying data.\n\n"
            "**Auth:** Admin or Super Admin.\n\n"
            "**Prerequisites:** The submission must exist."
        ),
        tags=["Admin — KYC Review"],
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
        verification = kyc_submission_service.approve_verification(
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
        tags=["Admin — KYC Review"],
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
        verification = kyc_submission_service.reject_verification(
            verification=self.get_object(),
            reviewer=request.user,
            rejection_reason=serializer.validated_data["rejection_reason"],
        )
        return Response(KYCVerificationAdminSerializer(verification).data)

    @extend_schema(
        summary="Flag a KYC submission for review",
        description=(
            "Flags a KYC submission for review with an optional "
            "`flag_reason` the submitter can act on when "
            "resubmitting.\n\n"
            "**Auth:** Admin or Super Admin.\n\n"
            "**Prerequisites:** The submission must exist."
        ),
        tags=["Admin — KYC Review"],
        request=KYCReviewFlagSerializer,
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
    def flag(self, request, pk=None):
        serializer = KYCReviewFlagSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        verification = kyc_submission_service.flag_verification(
            verification=self.get_object(),
            reviewer=request.user,
            flag_reason=serializer.validated_data["flag_reason"],
        )
        return Response(KYCVerificationAdminSerializer(verification).data)


# >>>>>>>>>>>>>>>>>>>>>>>>> Liveness View <<<<<<<<<<<<<<<<<<<<<<<<<<<<
class LivenessVerificationView(APIView):
    """
    Verifies that the supplied selfie is a real, live human.
    """

    permission_classes: ClassVar[list] = [IsAuthenticated]

    @extend_schema(
        summary="Verify a selfie liveness (SISSL)",
        description=(
            "Runs the supplied selfie against SISSL's liveness model. "
            "Pass = the photo is classified as a real, live human .\n\n"
            "**Cost discipline:** rate-limited to a fixed number of calls "
            "per user per hour. Excess attempts return 503 without hitting "
            "the vendor.\n\n"
            "**Important:** on a PASS, the capture is auto-saved -- NOT as the "
            "user's profile picture -- on the user object. This result is "
            "advisory: Another route (/api/v1/users/kyc/set-avatar/{user_id}/) is provided to set this selfie as the "
            "profile picture, if that is the decision."
        ),
        request=LivenessSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="LivenessVerificationResult",
                    fields={
                        "is_real": serializers.BooleanField(),
                        "score": serializers.FloatField(),
                        "liveness_threshold": serializers.FloatField(),
                    },
                ),
                description="Liveness passed",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "status": 200,
                            "success": True,
                            "message": "Liveness verified.",
                            "data": {"is_real": True, "score": 92.0, "liveness_threshold": 80.0},
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description="Liveness failed (not real / low score) or validation error",
                examples=[
                    OpenApiExample(
                        name="Error",
                        response_only=True,
                        value={
                            "success": False,
                            "status": 400,
                            "message": "Liveness failed (not real / low score) or validation error",
                            "technical_message": None,
                        },
                    ),
                ],
            ),
            503: OpenApiResponse(description="SISSL temporarily unavailable or rate-limit hit"),
        },
        tags=["Creator — KYC"],
    )
    def post(self, request):
        serializer = LivenessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = cast(dict[str, Any], serializer.validated_data)

        ip, ua = client_meta(request)

        try:
            result = SISSLServices.liveness(
                user=request.user,
                photo=data["photo"],
                ip=ip,
                user_agent=ua,
            )
        except SISSLLivenessFailed as exc:
            return custom_error_response(
                status=status.HTTP_400_BAD_REQUEST,
                message=str(exc),
            )
        except SISSLError as exc:
            return custom_error_response(
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
                message=str(exc),
            )

        # (Another route exists for setting the profile picture). URL
        # captures persist as-is; base64 captures are uploaded to Spaces
        # first. Non-raising by contract — a save failure must never reject
        # a verification that already passed SISSL.
        persist_liveness_avatar(request.user, data["photo"])

        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Liveness verified.",
            data={
                "is_real": result.is_real,
                "score": float(result.score),
                "liveness_threshold": float(result.threshold),
            },
        )


@extend_schema(
    summary="Set a user's profile picture",
    description=(
        "Sets a user's profile picture to the selfie that was previously "
        "captured and passed the SISSL liveness check. The target user is "
        "identified by the `user_id` path parameter.\n\n"
        "Call this after a liveness verification has passed and an admin has "
        "decided that the verified selfie should become the user's profile "
        "picture.\n\n"
        "**Auth:** Admin or Super Admin.\n\n"
        "**Prerequisites:** The target user must exist and must have a "
        "verified liveness selfie saved by the liveness endpoint.\n\n"
        "**Important:** This replaces the target user's current profile "
        "picture. The endpoint accepts no request body and is not an "
        "identity verification step; it only promotes the already-saved "
        "liveness selfie."
    ),
    tags=["Admin — KYC Review"],
    responses={
        200: inline_success_response(
            description="The target user's profile picture was updated.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value={
                        "status": 200,
                        "success": True,
                        "message": "Profile picture set successfully.",
                        "data": {"avatar_url": "https://cdn.example.com/selfie.jpg"},
                    },
                ),
            ],
        ),
        400: inline_error_response(
            description=("The target user has no saved liveness selfie, or the profile picture could not be stored."),
            examples=[
                OpenApiExample(
                    name="No liveness selfie",
                    value={
                        "errors": [
                            {
                                "type": "validation_error",
                                "code": "invalid",
                                "message": "Target user does not have a liveness selfie set.",
                                "field_name": None,
                            }
                        ]
                    },
                ),
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["not_found"],
    },
)
class LivenessAvatarSettingView(APIView):
    """
    Called by an admin to set the verified selfie as the profile picture.
    """

    permission_classes: ClassVar[list] = [IsAdminOrSuperAdminRole]

    def post(self, request, user_id, *args, **kwargs):

        ip, ua = client_meta(request)
        target_user = get_object_or_404(User, id=user_id)

        try:
            _ = SISSLServices.set_profile_picture(
                user=request.user,
                target_user=target_user,
                ip=ip,
                user_agent=ua,
            )
        except Exception as exc:
            return custom_error_response(
                status=status.HTTP_400_BAD_REQUEST,
                message=str(exc),
            )

        target_user.refresh_from_db()
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Profile picture set successfully.",
            data={
                "avatar_url": target_user.avatar_url,
            },
        )
