from shared.spectacular.responses import ErrorEnvelopeSerializer

"""
Admin views over the SISSLConfiguration singleton.

Two endpoints (both GET/PATCH on the same singleton row):
  GET    admin/config/   — current thresholds + HTTP knobs
  PATCH  admin/config/   — partial update

Why no POST: there is only ever ONE SISSLConfiguration row. Use the
`seed_sissl_config` management command (or the model admin) to create it
once; from then on, PATCH is the only thing the API surface should do.

Why no DELETE: deleting the singleton silently degrades every SISSL call
to env-var defaults. If you want to revert to defaults, PATCH the values.
"""

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.views import APIView

from api.sissl_verification.models import SISSLConfiguration
from api.sissl_verification.serializers.admin_sissl_configuration_serializer import (
    SISSLConfigurationSerializer,
)
from shared.response.error import custom_error_response
from shared.response.success import custom_success_response


# >>>>>>>>>>>>>>>>>>>>>>> Config View <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
class AdminSISSLConfigurationView(APIView):
    """
    GET and PATCH on the SISSLConfiguration singleton.
    """

    permission_classes = [IsAdminUser]


    # OpenAPI schema to help devs
    @extend_schema(
        summary="Get current SISSL configuration (admin only)",
        description=(
            "Returns the singleton config row. If the row has not been "
            "seeded yet, returns 404 — run the `seed_sissl_config` "
            "management command to create it."
        ),
        responses={
            200: OpenApiResponse(response=SISSLConfigurationSerializer),
            404: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description='SISSL configuration not seeded',
                examples=[
                    OpenApiExample(
                        name='Error',
                        response_only=True,
                        value={
                            'success': False,
                            'status': 404,
                            'message': 'SISSL configuration not seeded',
                            'technical_message': None,
                        },
                    ),
                ],
            ),
        },
        tags=["SISSL Verification — Admin"],
    )

    def get(self, request):
        config = SISSLConfiguration.current()

        if not config:
            return custom_error_response(
                status=status.HTTP_404_NOT_FOUND,
                message="SISSL configuration has not been seeded. Run `seed_sissl_config`.",
            )

        serializer = SISSLConfigurationSerializer(config)

        return custom_success_response(
            status=status.HTTP_200_OK,
            message="SISSL configuration fetched successfully.",
            data=serializer.data,
        )


    # OpenAPI schema to help devs
    @extend_schema(
        summary="Update SISSL configuration (admin only)",
        description=(
            "Partial update — send only the fields you want to change. "
            "Use this to tune thresholds without a redeploy.\n\n"
            "There is NO `verification_mode` field; verification is "
            "automatic, and a failed verification means the user cannot "
            "proceed. Do not request a bypass mode."
        ),
        request=SISSLConfigurationSerializer,
        responses={
            200: OpenApiResponse(response=SISSLConfigurationSerializer),
            400: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description='Validation error',
                examples=[
                    OpenApiExample(
                        name='Error',
                        response_only=True,
                        value={
                            'success': False,
                            'status': 400,
                            'message': 'Validation error',
                            'technical_message': None,
                        },
                    ),
                ],
            ),
            404: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description='SISSL configuration not seeded',
                examples=[
                    OpenApiExample(
                        name='Error',
                        response_only=True,
                        value={
                            'success': False,
                            'status': 404,
                            'message': 'SISSL configuration not seeded',
                            'technical_message': None,
                        },
                    ),
                ],
            ),
        },
        tags=["SISSL Verification — Admin"],
    )

    def patch(self, request):
        config = SISSLConfiguration.current()

        if not config:
            return custom_error_response(
                status=status.HTTP_404_NOT_FOUND,
                message="SISSL configuration has not been seeded. Run `seed_sissl_config`.",
            )

        serializer = SISSLConfigurationSerializer(
            config,
            data=request.data,
            partial=True,  # only validate the fields that were sent
        )
        serializer.is_valid(raise_exception=True)
        config = serializer.save()

        return custom_success_response(
            status=status.HTTP_200_OK,
            message="SISSL configuration updated successfully.",
            data=SISSLConfigurationSerializer(config).data,
        )
