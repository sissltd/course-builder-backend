from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import status, viewsets

from api.mie.models import SubmissionRejectionReason
from api.mie.serializers.rejection_reason_serializer import (
    RejectionReasonSerializer,
)
from api.users.permissions import IsSuperAdminRole


@extend_schema(tags=["Admin — MIE Submissions"])
class RejectionReasonAdminViewSet(viewsets.ModelViewSet):
    """Admin-managed rejection taxonomy backing dedup check #1 and the
    reject action's reason field. Reasons are soft-deactivated, never
    deleted, so historical submissions keep pointing at them.
    """

    queryset = SubmissionRejectionReason.objects.order_by("label")
    serializer_class = RejectionReasonSerializer
    permission_classes = [IsSuperAdminRole]
    lookup_field = "id"
    filterset_fields = ["is_active"]
    # The taxonomy is soft-deactivated (is_active=false), never deleted -
    # historical submissions keep pointing at their reasons.
    http_method_names = ["get", "post", "patch", "head", "options"]

    @extend_schema(
        summary="List rejection reasons",
        description="The taxonomy labels admins attach to rejections; also "
        "what dedup check #1 inherits onto short-circuited ideas. Filter "
        "with ?is_active=.",
        responses={status.HTTP_200_OK: RejectionReasonSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Create a rejection reason",
        description="Adds a label to the taxonomy. Labels are unique.",
        request=RejectionReasonSerializer,
        responses={status.HTTP_201_CREATED: RejectionReasonSerializer},
        examples=[
            OpenApiExample(
                "New reason",
                value={"label": "Duplicate of existing catalog", "description": "Titles already covered by a live course."},
            )
        ],
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(
        summary="Partially update a rejection reason",
        description=(
            "Edit the label/description or soft-deactivate with "
            "\"is_active\": false - deactivated reasons stop matching new "
            "ideas but stay on historical rows."
        ),
        request=RejectionReasonSerializer,
        responses={
            status.HTTP_200_OK: RejectionReasonSerializer,
            status.HTTP_404_NOT_FOUND: OpenApiResponse(description="Unknown reason id."),
        },
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(
        summary="Retrieve a rejection reason",
        description="One taxonomy entry by id.",
        responses={status.HTTP_200_OK: RejectionReasonSerializer},
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
