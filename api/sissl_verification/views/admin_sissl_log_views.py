
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.views import APIView

from api.sissl_verification.models import SISSLLog
from api.sissl_verification.serializers.readonly_sissl_log_serializer import (
    SISSLLogListSerializer,
    SISSLLogSerializer,
)
from shared.response.error import custom_error_response
from shared.response.success import custom_success_response
from shared.spectacular.responses import ErrorEnvelopeSerializer

"""
[IsAdminUser LOGIC]: Only admin users should be able to inspect verification
forensic records — this surface is used for cost reconciliation, vendor
health monitoring, and debugging "my verification didn't work" complaints.
"""


# >>>>>>>>>>>>>>>>>>>>>>> List Logs <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
class AdminSISSLLogListView(APIView):
    """
    Returns SISSL call logs, most recent first.

    Optional query filters:
      - kind     -> "liveness" | "bvn" | "nin"
      - status   -> "success" | "error"
      - user_id  -> UUID of a user
    """

    permission_classes = [IsAdminUser]


    # OpenAPI schema to help devs
    @extend_schema(
        operation_id="sissl_admin_logs_list",
        summary="List SISSL call logs (admin only)",
        description=(
            "Returns SISSL call logs (most recent first) with optional "
            "filters for triage and cost reconciliation."
        ),
        parameters=[
            OpenApiParameter(
                name="kind",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Filter by kind: liveness | bvn | nin",
                required=False,
            ),
            OpenApiParameter(
                name="status",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Filter by status: success | error",
                required=False,
            ),
            OpenApiParameter(
                name="user_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="Filter by user UUID",
                required=False,
            ),
        ],
        responses={
            200: OpenApiResponse(response=SISSLLogListSerializer(many=True)),
        },
        tags=["SISSL Verification — Admin"],
    )

    def get(self, request):
        # Build the queryset with optional filters
        queryset = SISSLLog.objects.select_related("user").order_by("-created_datetime")

        kind = request.query_params.get("kind")
        if kind:
            queryset = queryset.filter(kind=kind)

        log_status = request.query_params.get("status")
        if log_status:
            queryset = queryset.filter(status=log_status)

        user_id = request.query_params.get("user_id")
        if user_id:
            queryset = queryset.filter(user_id=user_id)

        serializer = SISSLLogListSerializer(queryset, many=True)

        return custom_success_response(
            status=status.HTTP_200_OK,
            message="SISSL logs fetched successfully.",
            data=serializer.data,
        )


# >>>>>>>>>>>>>>>>>>>>>>> Log Detail <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
class AdminSISSLLogDetailView(APIView):
    """
    Returns the full row for a single SISSLLog — including the redacted
    request/response summary JSON blobs that the list view omits.
    """

    permission_classes = [IsAdminUser]


    # OpenAPI schema to help devs
    @extend_schema(
        operation_id="sissl_admin_logs_retrieve",
        summary="Get a single SISSL log (admin only)",
        parameters=[
            OpenApiParameter(
                name="log_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description="The SISSLLog UUID",
            )
        ],
        responses={
            200: OpenApiResponse(response=SISSLLogSerializer),
            404: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description='Log not found',
                examples=[
                    OpenApiExample(
                        name='Error',
                        response_only=True,
                        value={
                            'success': False,
                            'status': 404,
                            'message': 'Log not found',
                            'technical_message': None,
                        },
                    ),
                ],
            ),
        },
        tags=["SISSL Verification — Admin"],
    )

    def get(self, request, log_id):
        try:
            log = SISSLLog.objects.select_related("user").get(id=log_id)
        except SISSLLog.DoesNotExist:
            return custom_error_response(
                status=status.HTTP_404_NOT_FOUND,
                message="SISSL log not found.",
            )

        serializer = SISSLLogSerializer(log)

        return custom_success_response(
            status=status.HTTP_200_OK,
            message="SISSL log fetched successfully.",
            data=serializer.data,
        )
