from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status, viewsets

from api.mie.models import SubmissionRejectionReason
from api.mie.serializers.rejection_reason_serializer import (
    RejectionReasonSerializer,
)
from api.users.permissions import IsSuperAdminRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES


@extend_schema(tags=["Admin — MIE Rejection Reasons"])
class RejectionReasonAdminViewSet(viewsets.ModelViewSet):
    """Admin-managed rejection taxonomy backing dedup check #1."""

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
        description=(
            "Returns all rejection-reason taxonomy entries, ordered by label. "
            "These labels are what admins attach when rejecting a submission "
            "and what dedup check #1 inherits onto short-circuited ideas. "
            "Filter with ?is_active= to show only active or inactive reasons.\n\n"

            "Called from the admin rejection-reasons management page and when "
            "rendering the rejection-reason dropdown in the submission review "
            "screen.\n\n"

            "**Auth:** Requires the Super Admin role.\n\n"

            "**Prerequisites:** None.\n\n"

            "**Important:** Deactivated reasons (is_active=false) stop "
            "matching new ideas but remain on historical submissions. They "
            "are never hard-deleted."
        ),
        responses={
            status.HTTP_200_OK: RejectionReasonSerializer(many=True),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Create a rejection reason",
        description=(
            "Adds a new label to the rejection-reason taxonomy. Labels are "
            "unique across the taxonomy and are used by the reject action's "
            "reason field and by dedup check #1 when short-circuiting "
            "previously rejected ideas.\n\n"

            "Called from the admin rejection-reasons management page when a "
            "new rejection category is needed.\n\n"

            "**Auth:** Requires the Super Admin role.\n\n"

            "**Prerequisites:** None.\n\n"

            "**Important:** The label must be unique — submitting a duplicate "
            "label returns a 400 validation error."
        ),
        request=RejectionReasonSerializer,
        examples=[
            OpenApiExample(
                name="New reason",
                request_only=True,
                value={
                    "label": "Duplicate of existing catalog",
                    "description": "Titles already covered by a live course.",
                },
            ),
        ],
        responses={
            status.HTTP_201_CREATED: RejectionReasonSerializer,
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(
        summary="Partially update a rejection reason",
        description=(
            "Updates one or more fields on an existing rejection-reason "
            "taxonomy entry. Use this to correct a label, improve the "
            "description, or soft-deactivate a reason by setting "
            "\"is_active\": false.\n\n"

            "Called from the admin rejection-reasons management page when "
            "editing or deactivating a reason.\n\n"

            "**Auth:** Requires the Super Admin role.\n\n"

            "**Prerequisites:** The rejection reason with the given id must "
            "exist.\n\n"

            "**Important:** Deactivated reasons stop matching new ideas but "
            "stay on historical rows. This is a partial update — only send "
            "the fields you want to change."
        ),
        request=RejectionReasonSerializer,
        responses={
            status.HTTP_200_OK: RejectionReasonSerializer,
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(
        summary="Retrieve a rejection reason",
        description=(
            "Returns a single rejection-reason taxonomy entry by its id. "
            "Includes the label, description, and active status.\n\n"

            "Called from the admin rejection-reasons management page when "
            "viewing the details of a specific reason.\n\n"

            "**Auth:** Requires the Super Admin role.\n\n"

            "**Prerequisites:** The rejection reason with the given id must "
            "exist.\n\n"

            "**Important:** None."
        ),
        responses={
            status.HTTP_200_OK: RejectionReasonSerializer,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
