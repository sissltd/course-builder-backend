from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from api.mie.models import DeveloperAccount
from api.mie.serializers.developer_admin_serializer import (
    DeveloperAccountAdminSerializer,
    DeveloperActionResponseSerializer,
    DeveloperApprovalResponseSerializer,
    DeveloperRegisterSerializer,
)
from api.mie.services import developer_service
from api.users.permissions import IsSuperAdminRole


@extend_schema(tags=["Admin — MIE Developers"])
class MieDeveloperAdminViewSet(viewsets.ReadOnlyModelViewSet):
    """Superadmin lifecycle management for external MIE developers.

    Registration creates a PENDING account; approval issues the API key
    that is shown exactly once in the approve response. Rejection is
    reachable from any state (decisions are reversible) and revokes key
    material; re-approval issues fresh credentials.
    """

    queryset = DeveloperAccount.objects.order_by("-created_datetime")
    serializer_class = DeveloperAccountAdminSerializer
    permission_classes = [IsSuperAdminRole]
    lookup_field = "id"
    filterset_fields = ["status", "plan_type"]
    search_fields = ["email"]

    @extend_schema(
        summary="List developers",
        description=(
            "Paginated developer directory. Filter with ?status= and "
            "?plan_type=; search by email with ?search=. Never contains "
            "key material - only the masked key preview."
        ),
        responses={status.HTTP_200_OK: DeveloperAccountAdminSerializer},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Retrieve a developer",
        description=(
            "Full admin representation of one developer account, including "
            "key issuance/last-use timestamps and decision history fields."
        ),
        responses={status.HTTP_200_OK: DeveloperAccountAdminSerializer},
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        summary="Register a developer (manual onboarding)",
        description=(
            "Superadmin-side account creation for manual onboarding. The "
            "normal path is the developer self-registering via "
            "POST /api/v1/mie/v1/register/ - both land in PENDING and "
            "need this surface's approve action to become active."
        ),
        request=DeveloperRegisterSerializer,
        responses={
            status.HTTP_201_CREATED: DeveloperAccountAdminSerializer,
            status.HTTP_400_BAD_REQUEST: DeveloperAccountAdminSerializer,
        },
        examples=[
            OpenApiExample(
                "Registration",
                value={"email": "dev@studio.io", "webhook_url": "https://hooks.studio.io/mie"},
                request_only=True,
            )
        ],
    )
    def create(self, request, *args, **kwargs):
        serializer = DeveloperRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = developer_service.register_developer(**serializer.validated_data)
        return Response(
            DeveloperAccountAdminSerializer(account).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Approve a developer",
        description=(
            "Approve a PENDING, REJECTED or SUSPENDED account. When "
            "credentials are freshly issued the response carries the full "
            "API key exactly once; when null, existing credentials remain "
            "valid. The raw key is never retrievable again afterwards."
        ),
        request=None,
        responses={status.HTTP_200_OK: DeveloperApprovalResponseSerializer},
    )
    @action(detail=True, methods=["post"])
    def approve(self, request, id=None):
        account = self.get_object()
        raw_key = developer_service.approve_developer(actor=request.user, account=account)
        return Response(
            {
                "account": DeveloperAccountAdminSerializer(account).data,
                "one_time_api_key": raw_key,
            }
        )

    @extend_schema(
        summary="Reject a developer",
        description=(
            "Reject the account from any state and revoke its credentials "
            "immediately. Reversible: approving later issues fresh keys."
        ),
        request=None,
        responses={status.HTTP_200_OK: DeveloperActionResponseSerializer},
    )
    @action(detail=True, methods=["post"])
    def reject(self, request, id=None):
        account = self.get_object()
        developer_service.reject_developer(actor=request.user, account=account)
        return Response(
            {"detail": f"{account.email} rejected; credentials revoked."}
        )

    @extend_schema(
        summary="Suspend a developer",
        description=(
            "Freeze an APPROVED account: its credentials stop working "
            "immediately but queue history is retained untouched. Only "
            "approved accounts can be suspended."
        ),
        request=None,
        responses={status.HTTP_200_OK: DeveloperActionResponseSerializer},
    )
    @action(detail=True, methods=["post"])
    def suspend(self, request, id=None):
        account = self.get_object()
        developer_service.suspend_developer(actor=request.user, account=account)
        return Response({"detail": f"{account.email} suspended."})
