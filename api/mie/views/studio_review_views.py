from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers, exceptions

from api.mie.authentication import MieDeveloperAuthentication
from api.mie.permissions import IsMieDeveloper
from api.mie.serializers.studio_review_serializer import (
    StudioReviewReceiptSerializer,
    StudioReviewSerializer,
)
from api.mie.services.studio_review_service import accept_review
from api.mie.throttling import MieDeveloperRateThrottle
from shared.serializers.storage_serializer import UploadRequestSerializer
from shared.services.storage_service import (
    InvalidFileType,
    FileTooLarge,
    InvalidUploadMetadata,
    StorageError,
)


class MieStudioReviewView(APIView):
    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]
    throttle_scope = "mie_course_push"
    throttle_classes = [MieDeveloperRateThrottle]

    @extend_schema(
        tags=["Developer — MIE Courses"],
        summary="Deliver an exact Studio text or video version for review",
        request=StudioReviewSerializer,
        responses={200: StudioReviewReceiptSerializer},
    )
    def post(self, request):
        serializer = StudioReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            return Response(
                accept_review(
                    developer=request.auth,
                    data=serializer.validated_data,
                    idempotency_key=request.headers.get("Idempotency-Key", ""),
                )
            )
        except StorageError as exc:
            raise exceptions.APIException(
                "Platform media storage is unavailable."
            ) from exc


class StudioUploadSerializer(UploadRequestSerializer):
    submission_id = serializers.UUIDField()
    asset_id = serializers.UUIDField()
    sha256 = serializers.RegexField(r"^[a-f0-9]{64}$")
    size = serializers.IntegerField(min_value=1, max_value=100 * 1024 * 1024)


class MieStudioUploadView(MieStudioReviewView):
    @extend_schema(
        tags=["Developer — MIE Courses"],
        summary="Reserve or resume a Studio media upload",
        request=StudioUploadSerializer,
        responses={200: dict},
    )
    def post(self, request):
        from api.mie.services.studio_upload_service import request_upload

        serializer = StudioUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        submission_id = data.pop("submission_id")
        asset_id = data.pop("asset_id")
        data.pop("folder", None)
        try:
            return Response(
                request_upload(
                    developer=request.auth,
                    submission_id=submission_id,
                    asset_id=asset_id,
                    metadata=data,
                )
            )
        except (InvalidFileType, FileTooLarge, InvalidUploadMetadata) as exc:
            raise exceptions.ValidationError(str(exc)) from exc
        except StorageError as exc:
            raise exceptions.APIException(
                "Platform media storage is unavailable."
            ) from exc
