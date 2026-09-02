from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from api.catalog.models import CategoryRequest
from api.catalog.serializers import (
    CategoryRequestApproveSerializer,
    CategoryRequestCreateSerializer,
    CategoryRequestSerializer,
)
from api.catalog.services import category_request_service
from api.users.permissions import IsAdminRole, IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

MANAGE_ACTIONS = {"approve", "reject"}

_REQUEST_EXAMPLE = {
    "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "name": "Data Science",
    "description": "Courses about data analysis and machine learning.",
    "status": "PENDING",
    "resulting_category": None,
    "requested_by_email": "creator@example.com",
    "reviewed_at": None,
    "created_datetime": "2026-09-01T10:00:00Z",
}


@extend_schema(tags=["Creator — Category Requests"])
class CategoryRequestViewSet(ModelViewSet):
    """A creator's requests for a Category that does not exist yet, plus
    the Admin approve/reject surface.

    Approving creates the real Category and emails the requester; the
    price is set by the approving admin, not the requester. Creators see
    only their own requests; admins see every request.
    """

    permission_classes = [IsCourseCreatorRole | IsAdminRole]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CategoryRequest.objects.none()

        queryset = CategoryRequest.objects.select_related(
            "requested_by", "resulting_category"
        )
        if self.request.user.is_superuser or self.request.user.role in (
            IsAdminRole.allowed_roles
        ):
            return queryset
        return queryset.filter(requested_by=self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return CategoryRequestCreateSerializer
        return CategoryRequestSerializer

    def get_permissions(self):
        if self.action in MANAGE_ACTIONS:
            return [IsAdminRole()]
        return super().get_permissions()

    @extend_schema(
        summary="Request a new category",
        description=(
            "Files a Pending request for a category that does not exist "
            "yet, and notifies admins in-app.\\n\\n"
            "Call this from the \\u201ccan't find your preferred category?\\u201d "
            "link on course creation. The category is **not** usable until "
            "an admin approves it \\u2014 the creator is emailed when that "
            "happens.\\n\\n"
            "**Auth:** Course Creator or Admin.\\n\\n"
            "**Prerequisites:** None.\\n\\n"
            "**Important:** A name matching an existing category is not "
            "rejected automatically \\u2014 whether it is a real duplicate "
            "is left to the reviewing admin. Nothing is created in the "
            "catalog at this point."
        ),
        request=CategoryRequestCreateSerializer,
        examples=[
            OpenApiExample(
                "Request",
                request_only=True,
                value={
                    "name": "Data Science",
                    "description": "Courses about data analysis and machine learning.",
                },
            )
        ],
        responses={
            201: OpenApiResponse(
                response=CategoryRequestSerializer,
                description="The Pending request.",
                examples=[OpenApiExample("Created", value=_REQUEST_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(
        summary="List category requests",
        description=(
            "Returns category requests, newest first. A Course Creator sees "
            "only their own; an Admin sees every request.\\n\\n"
            "Call this to show a creator the state of what they asked for, "
            "or to populate the admin review queue.\\n\\n"
            "**Auth:** Course Creator or Admin.\\n\\n"
            "**Prerequisites:** None.\\n\\n"
            "**Important:** Scoping is server-side \\u2014 no query "
            "parameter lets a creator see another creator's requests."
        ),
        responses={
            200: OpenApiResponse(
                response=CategoryRequestSerializer(many=True),
                description="Category requests visible to the caller.",
                examples=[OpenApiExample("Success", value=[_REQUEST_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Retrieve a category request",
        description=(
            "Returns one category request.\\n\\n"
            "**Auth:** Course Creator (own requests only) or Admin.\\n\\n"
            "**Prerequisites:** The request must exist and be visible to "
            "the caller.\\n\\n"
            "**Important:** A creator requesting another creator's request "
            "gets 404, not 403 \\u2014 existence is not leaked."
        ),
        responses={
            200: OpenApiResponse(
                response=CategoryRequestSerializer,
                examples=[OpenApiExample("Success", value=_REQUEST_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        summary="Approve a category request",
        description=(
            "Approves a Pending request: creates the real Category with the "
            "supplied creator price, links it to the request, and emails "
            "the requester.\\n\\n"
            "**Auth:** Admin.\\n\\n"
            "**Prerequisites:** The request must be Pending.\\n\\n"
            "**Important:** `creator_price` is required and is set by you, "
            "not the requester \\u2014 it is what the platform pays per "
            "approved course in this category. A name or slug colliding "
            "with an existing category returns 400 and nothing is created. "
            "If the notification email fails the approval still stands."
        ),
        request=CategoryRequestApproveSerializer,
        examples=[
            OpenApiExample(
                "Approve", request_only=True, value={"creator_price": "150000.00"}
            )
        ],
        responses={
            200: OpenApiResponse(
                response=CategoryRequestSerializer,
                description="The approved request, with the new category attached.",
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
            request=self.get_object(),
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(CategoryRequestSerializer(updated).data)

    @extend_schema(
        summary="Reject a category request",
        description=(
            "Closes a Pending request without creating a Category.\\n\\n"
            "**Auth:** Admin.\\n\\n"
            "**Prerequisites:** The request must be Pending.\\n\\n"
            "**Important:** No email is sent \\u2014 there is no "
            "rejection-notice screen. The request is retained for history."
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                response=CategoryRequestSerializer,
                description="The rejected request.",
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
        return Response(CategoryRequestSerializer(updated).data)
