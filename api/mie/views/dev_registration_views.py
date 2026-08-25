from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
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


@extend_schema(tags=["MIE Developer — Onboarding"])
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
            "Open registration for external developers. Creates a PENDING "
            "account from an email and webhook URL - it cannot "
            "authenticate anything until a superadmin approves it, at "
            "which point an API key is issued and shown exactly once. "
            "Approval happens out-of-band; watch your webhook URL and "
            "inbox."
        ),
        request=DeveloperRegisterSerializer,
        responses={
            status.HTTP_201_CREATED: DeveloperAccountAdminSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(
                description="Invalid payload or email already registered."
            ),
            status.HTTP_429_TOO_MANY_REQUESTS: OpenApiResponse(
                description="Too many registration attempts; retry later."
            ),
        },
        examples=[
            OpenApiExample(
                "Registration",
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
