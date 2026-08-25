from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.mie.authentication import MieDeveloperAuthentication
from api.mie.permissions import IsMieDeveloper
from api.mie.serializers.dev_me_serializer import DeveloperMeSerializer
from api.mie.services import documentation_service


@extend_schema(tags=["MIE Developer — Account"])
class MieDeveloperMeView(APIView):
    """The authenticated developer's own account snapshot."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]

    @extend_schema(
        summary="Show your account",
        description=(
            "Your developer account: status, payout plan, webhook URL, "
            "masked API key (full key is never shown again), signing "
            "secret for verifying our webhooks, and key usage timestamps. "
            "A 200 from this endpoint also doubles as a credentials "
            "health-check."
        ),
        responses={
            status.HTTP_200_OK: DeveloperMeSerializer,
            status.HTTP_401_UNAUTHORIZED: OpenApiResponse(
                description="Missing/invalid credentials or account not active."
            ),
        },
    )
    def get(self, request):
        return Response(DeveloperMeSerializer(request.auth).data)


@extend_schema(tags=["MIE Developer — Account"])
class MieDocumentationView(APIView):
    """Machine-readable integration documentation for this developer.

    Generated from live code constants - plan type, endpoints, reference
    scheme, and one signed-webhook sample per event type - so it always
    matches what the API actually does.
    """

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]

    @extend_schema(
        summary="Integration documentation",
        description=(
            "Everything an integrator needs in one payload: the plan you "
            "are on and its payout semantics, every available endpoint "
            "with request/response examples, the SCB-xxxxxxxx-S reference "
            "suffix table, how authentication headers work, and a sample "
            "webhook body plus HMAC verification recipe for each event "
            "type."
        ),
        responses={
            status.HTTP_200_OK: OpenApiResponse(
                description=(
                    "JSON object with sections: plan, authentication, "
                    "reference_scheme, endpoints, webhooks."
                ),
            ),
            status.HTTP_401_UNAUTHORIZED: OpenApiResponse(
                description="Missing/invalid credentials or account not active."
            ),
        },
    )
    def get(self, request):
        return Response(documentation_service.build_documentation(request.auth))
