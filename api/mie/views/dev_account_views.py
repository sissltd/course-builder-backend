from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from api.mie.authentication import MieDeveloperAuthentication
from api.mie.permissions import IsMieDeveloper
from api.mie.serializers.dev_me_serializer import DeveloperMeSerializer
from api.mie.services import documentation_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES


@extend_schema(tags=["Developer — MIE Account"])
class MieDeveloperMeView(APIView):
    """The authenticated developer's own account snapshot."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]

    @extend_schema(
        summary="Show your account",
        description=(
            "Return the authenticated developer's account details: current "
            "status, payout plan, webhook URL, masked API key, signing "
            "secret for verifying inbound webhooks, and key usage "
            "timestamps. The full API key is never returned after "
            "initial creation.\n\n"

            "Call this endpoint to verify that credentials are working or "
            "to retrieve account details before building an integration. "
            "A 200 response doubles as a credentials health-check.\n\n"

            "**Auth:** Requires a valid MIE developer API key.\n\n"

            "**Prerequisites:** The developer account must be in ACTIVE "
            "status (approved by a superadmin).\n\n"

            "**Important:** The API key is shown in full only once at "
            "approval time; this endpoint always returns a masked preview. "
            "If the account is in PENDING or SUSPENDED status, this "
            "endpoint returns 401."
        ),
        responses={
            200: OpenApiResponse(
                response=DeveloperMeSerializer,
                description="Authenticated developer's account snapshot.",
                examples=[
                    OpenApiExample(
                        "Account snapshot",
                        value={
                            "id": "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11",
                            "email": "dev@studio.io",
                            "status": "ACTIVE",
                            "payout_plan": "standard",
                            "webhook_url": "https://hooks.studio.io/mie",
                            "api_key_preview": "sk_live_a1b2...xyz9",
                            "signing_secret": "whsec_••••••••",
                            "last_key_used_at": "2025-06-15T10:30:00Z",
                            "created_at": "2025-03-01T08:00:00Z",
                        },
                        response_only=True,
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        return Response(DeveloperMeSerializer(request.auth).data)


@extend_schema(tags=["Developer — MIE Account"])
class MieDocumentationView(APIView):
    """Machine-readable integration documentation for this developer."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]

    @extend_schema(
        summary="Integration documentation",
        description=(
            "Return a complete machine-readable reference for the "
            "developer's integration: plan type and payout semantics, "
            "every available endpoint with request and response examples, "
            "the SCB-xxxxxxxx-S reference suffix table, authentication "
            "header format, and a sample signed webhook body per event "
            "type.\n\n"

            "Call this endpoint to bootstrap a client SDK, verify a "
            "reference suffix, or reconstruct the onboarding documentation "
            "after losing the original email. The payload is always "
            "generated from live code constants and reflects the current "
            "API surface.\n\n"

            "**Auth:** Requires a valid MIE developer API key.\n\n"

            "**Prerequisites:** The developer account must be in ACTIVE "
            "status.\n\n"

            "**Important:** This endpoint is read-only and idempotent — "
            "calling it multiple times returns identical content unless "
            "the plan or endpoint set has changed server-side."
        ),
        responses={
            200: OpenApiResponse(
                description=(
                    "JSON object with sections: plan, authentication, "
                    "reference_scheme, endpoints, webhooks."
                ),
                examples=[
                    OpenApiExample(
                        "Documentation payload",
                        value={
                            "plan": {
                                "name": "standard",
                                "payout_schedule": "net-30",
                                "minimum_payout": 50.00,
                            },
                            "authentication": {
                                "header": "X-MIE-API-Key",
                                "algorithm": "HMAC-SHA256",
                            },
                            "reference_scheme": {
                                "prefix": "SCB-",
                                "suffix": "-S",
                                "example": "SCB-a1b2c3d4-S",
                            },
                            "endpoints": [
                                {
                                    "path": "/api/v1/mie/submit/",
                                    "method": "POST",
                                    "description": "Submit a new transaction.",
                                }
                            ],
                            "webhooks": {
                                "events": [
                                    "transaction.completed",
                                    "transaction.failed",
                                ],
                                "signature_header": "X-MIE-Signature",
                                "sample_body": '{"event": "transaction.completed", ...}',
                            },
                        },
                        response_only=True,
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        return Response(documentation_service.build_documentation(request.auth))
