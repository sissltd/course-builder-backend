from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import exceptions
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shared.serializers.storage_serializer import (
    FileAccessRequestSerializer,
    FileAccessResponseSerializer,
    UploadRequestSerializer,
    UploadResponseSerializer,
)
from shared.services.storage_service import (
    PRESIGN_EXPIRY,
    FileTooLarge,
    InvalidFileType,
    InvalidUploadMetadata,
    StorageError,
    StorageService,
)


class UploadPresignView(APIView):
    """Broker a presigned upload URL for direct-to-storage uploads (avatars,
    certificates, videos, etc.) via StorageService - the backend never
    touches the file bytes, it only hands out a short-lived signed PUT URL
    and a short-lived signed GET URL for reading it after upload."""

    permission_classes = [IsAuthenticated]
    serializer_class = UploadRequestSerializer

    @extend_schema(
        summary="Request a presigned upload URL",
        tags=["Creator — Uploads"],
        responses={200: OpenApiResponse(response=UploadResponseSerializer)},
    )
    def post(self, request):
        serializer = UploadRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = StorageService.request_upload(**serializer.validated_data)
        except (InvalidFileType, FileTooLarge, InvalidUploadMetadata) as exc:
            raise exceptions.ValidationError(str(exc)) from exc
        except StorageError as exc:
            raise exceptions.APIException(str(exc)) from exc

        return Response(UploadResponseSerializer(result).data)


class UploadAccessView(APIView):
    """Create a fresh read URL for a private uploaded object."""

    permission_classes = [IsAuthenticated]
    serializer_class = FileAccessRequestSerializer

    @extend_schema(
        summary="Refresh a private file URL",
        description=(
            "Returns a temporary URL for viewing an uploaded private object.\n\n"
            "**Auth:** Any authenticated user with the durable `file_key`.\n\n"
            "**Prerequisites:** The object must have been uploaded to the configured "
            "Space.\n\n"
            "**Important:** The returned URL expires after 10 minutes; call this "
            "endpoint again when playback starts after expiry."
        ),
        tags=["Creator — Uploads"],
        request=FileAccessRequestSerializer,
        responses={200: OpenApiResponse(response=FileAccessResponseSerializer)},
    )
    def post(self, request):
        serializer = FileAccessRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        file_url = StorageService.generate_presigned_get(
            serializer.validated_data["file_key"]
        )
        if not file_url:
            raise exceptions.APIException("Failed to generate file access URL.")

        return Response(
            FileAccessResponseSerializer(
                {"file_url": file_url, "expires_in": PRESIGN_EXPIRY}
            ).data
        )
