from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import exceptions
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shared.serializers.storage_serializer import (
    UploadRequestSerializer,
    UploadResponseSerializer,
)
from shared.services.storage_service import (
    InvalidFileType,
    StorageError,
    StorageService,
)


class UploadPresignView(APIView):
    """Broker a presigned upload URL for direct-to-storage uploads (avatars,
    certificates, videos, etc.) via StorageService - the backend never
    touches the file bytes, it only hands out a short-lived signed PUT URL
    and the CDN URL the file will live at once uploaded."""

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
        except InvalidFileType as exc:
            raise exceptions.ValidationError(str(exc)) from exc
        except StorageError as exc:
            raise exceptions.APIException(str(exc)) from exc

        return Response(UploadResponseSerializer(result).data)
