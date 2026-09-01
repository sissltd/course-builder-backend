from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.mie.serializers.developer_admin_serializer import (
    DeveloperAccountAdminSerializer,
    DeveloperRegisterSerializer,
)
from api.mie.services import developer_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES


@extend_schema(tags=["Public — MIE Registration"], auth=[{}])
class MieDeveloperRegistrationView(APIView):
    """Self-service developer registration (step 1 of the journey).

    Public by design: the developer registers their own email + webhook
    URL and lands in PENDING, which authenticates nothing. A superadmin
    reviews and approves (issuing the API key) via the admin surface.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "mie_register"

    @extend_schema(
        summary="Register as a developer",
        description=(
            "Create a new MIE developer account in PENDING status. The "
            "account cannot authenticate or access any API resources until "
            "a superadmin approves it out-of-band and issues an API key, "
            "which is shown exactly once at approval time.\n\n"

            "Call this endpoint when a developer first wants to join the "
            "MIE platform. After submission, the developer should watch "
            "their webhook URL and inbox for the approval notification.\n\n"

            "**Auth:** Public — no credentials required.\n\n"

            "**Prerequisites:** None.\n\n"

            "**Important:** The email address must be unique across all "
            "registrations. This endpoint is rate-limited per client IP; "
            "exceeding the limit returns 429 with a Retry-After header. "
            "Registration is idempotent for the same email — a duplicate "
            "request returns the existing pending account."
        ),
        request=DeveloperRegisterSerializer,
        responses={
            status.HTTP_201_CREATED: DeveloperAccountAdminSerializer,
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["rate_limited"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
        examples=[
            OpenApiExample(
                "Registration request",
                value={
                    "email": "dev@studio.io",
                    "webhook_url": "https://hooks.studio.io/mie",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Created (pending approval)",
                value={
                    "id": "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11",
                    "email": "dev@studio.io",
                    "status": "PENDING",
                    "api_key_preview": None,
                },
                response_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = DeveloperRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = developer_service.register_developer(**serializer.validated_data)
        return Response(
            DeveloperAccountAdminSerializer(account).data,
            status=status.HTTP_201_CREATED,
        )
