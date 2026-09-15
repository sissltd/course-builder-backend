from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters as drf_filters
from rest_framework.viewsets import ReadOnlyModelViewSet

from api.catalog.filters import AdminCategoryRequestFilter
from api.catalog.models import CategoryRequest
from api.catalog.serializers import (
    AdminCategoryRequestSerializer,
    CategoryRequestApproveSerializer,
)
from api.catalog.services import category_request_service
from api.users.permissions import CanManageCategories
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_CATEGORY_REQUEST_EXAMPLE = {
    "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "name": "Data Science",
    "description": "Courses about data analysis and machine learning.",
    "status": "PENDING",
    "resulting_category": None,
    "requested_by_email": "creator@example.com",
    "reviewed_at": None,
    "created_datetime": "2026-09-01T10:00:00Z",
    "requested_by": {
        "id": "5f4d3c2b-1a09-48e7-b6a5-9c8d7e6f5a4b",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "creator@example.com",
    },
    "reviewed_by": None,
}


@extend_schema_view(
    list=extend_schema(
        summary="List all category requests",
        description=(
            "Returns every creator category request for the review queue, newest "
            "first. The payload includes the request status, requested details, "
            "and requester/reviewer identities.\n\n"
            "Call this when the Admin Writer opens the category-request queue, "
            "then use the detail or approve/reject actions for a selected row.\n\n"
            "**Auth:** Admin Writer — Writer, Admin, or Super Admin.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** This is the admin-wide queue; it is not scoped to "
            "the authenticated user's own requests. Use `status` in the client "
            "to separate Pending, Approved, and Rejected work."
        ),
        tags=["Admin — Category Requests"],
        responses={
            200: OpenApiResponse(
                response=AdminCategoryRequestSerializer(many=True),
                description="All category requests visible to the Admin Writer.",
                examples=[OpenApiExample("Success", value=[_CATEGORY_REQUEST_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a category request",
        description=(
            "Returns one category request for the Admin Writer review panel.\n\n"
            "Call this after selecting a row from the category-request queue.\n\n"
            "**Auth:** Admin Writer — Writer, Admin, or Super Admin.\n\n"
            "**Prerequisites:** The request must exist.\n\n"
            "**Important:** The payload includes requester and reviewer identity "
            "for the admin audit trail."
        ),
        tags=["Admin — Category Requests"],
        responses={
            200: OpenApiResponse(
                response=AdminCategoryRequestSerializer,
                examples=[OpenApiExample("Success", value=_CATEGORY_REQUEST_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class AdminCategoryRequestViewSet(ReadOnlyModelViewSet):
    """Admin Writer queue for every category request."""

    permission_classes = [CanManageCategories]
    serializer_class = AdminCategoryRequestSerializer
    filterset_class = AdminCategoryRequestFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["created_datetime", "reviewed_at", "name", "status"]
    ordering = ["-created_datetime"]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CategoryRequest.objects.none()
        return CategoryRequest.objects.select_related(
            "requested_by", "reviewed_by", "resulting_category"
        )

    @extend_schema(
        summary="Approve a category request",
        description=(
            "Approves a Pending category request and creates the real Category.\n\n"
            "Call this after reviewing the request and deciding its starting "
            "creator payout.\n\n"
            "**Auth:** Admin Writer — Writer, Admin, or Super Admin.\n\n"
            "**Prerequisites:** The request must be Pending.\n\n"
            "**Important:** `creator_price` seeds all three category price tiers. "
            "A duplicate category name or slug returns 400 and creates nothing."
        ),
        tags=["Admin — Category Requests"],
        request=CategoryRequestApproveSerializer,
        responses={
            200: OpenApiResponse(
                response=AdminCategoryRequestSerializer,
                examples=[
                    OpenApiExample(
                        "Approved",
                        value={
                            **_CATEGORY_REQUEST_EXAMPLE,
                            "status": "APPROVED",
                            "resulting_category": {
                                "id": "5f4d3c2b-1a09-48e7-b6a5-9c8d7e6f5a4b",
                                "name": "Data Science",
                            },
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        serializer = CategoryRequestApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = category_request_service.approve_request(
            request=self.get_object(), actor=request.user, **serializer.validated_data
        )
        return Response(self.get_serializer(updated).data)

    @extend_schema(
        summary="Reject a category request",
        description=(
            "Rejects a Pending category request without creating a Category.\n\n"
            "Call this after reviewing a request that should not enter the "
            "catalog.\n\n"
            "**Auth:** Admin Writer — Writer, Admin, or Super Admin.\n\n"
            "**Prerequisites:** The request must be Pending.\n\n"
            "**Important:** The request remains in the queue as Rejected for history."
        ),
        tags=["Admin — Category Requests"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=AdminCategoryRequestSerializer,
                examples=[
                    OpenApiExample(
                        "Rejected",
                        value={**_CATEGORY_REQUEST_EXAMPLE, "status": "REJECTED"},
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        updated = category_request_service.reject_request(
            request=self.get_object(), actor=request.user
        )
        return Response(self.get_serializer(updated).data)
