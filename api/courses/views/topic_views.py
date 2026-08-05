from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import filters as drf_filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from api.courses.filters import TopicFilter
from api.courses.models import Topic
from api.courses.serializers import TopicSerializer, TopicWriteSerializer
from api.courses.services import topic_reservation_service
from api.users.permissions import IsAdminRole, IsCreatorReviewerRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

WRITE_ACTIONS = {"create", "update", "partial_update", "destroy"}

_TOPIC_EXAMPLE = {
    "id": "e5f6a7b8-c9d0-4e1f-2a3b-4c5d6e7f8a9b",
    "category": {"id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74", "name": "Software Engineering"},
    "name": "Django REST Framework",
    "creator_price": "180.00",
    "status": "ACTIVE",
    "reserved_by": None,
    "reserved_until": None,
    "is_currently_reserved": False,
    "created_datetime": "2026-07-12T09:30:11.204Z",
    "updated_datetime": "2026-07-19T06:04:45.882Z",
}

_AUTH_LINE = (
    "**Auth:** Admin or Creator Reviewer/Verifier. Everyone else has read "
    "access only - creators browse topics under a category before creating "
    "a course."
)


@extend_schema_view(
    list=extend_schema(
        summary="List topics",
        description=(
            "Returns every topic, each nested under its category with "
            "current price, status, and reservation state. This is the "
            "lookup creators use to narrow a course into a specific topic, "
            "and the table behind the admin Topics screen.\n\n"
            "Called when rendering the topic picker during course creation "
            "(after a category is chosen), and whenever the Topics screen "
            "loads.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None beyond being signed in.\n\n"
            "**Important:** Filter with `?category=<id>` to scope to one "
            "category, and `?status=ACTIVE` to exclude topics not accepting "
            "submissions. `is_currently_reserved` reflects `reserved_until` "
            "against the current date - a reservation that has expired "
            "reads as `false` even if `reserved_by`/`reserved_until` are "
            "still set. Results are paginated."
        ),
        tags=["Courses — Topics"],
        responses={
            200: OpenApiResponse(
                response=TopicSerializer(many=True),
                description="Topics, ordered by name.",
                examples=[OpenApiExample(name="Success", value=[_TOPIC_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a topic",
        description=(
            "Returns a single topic by id.\n\n"
            "Called when opening a topic's detail or edit view.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None beyond being signed in.\n\n"
            "**Important:** None."
        ),
        tags=["Courses — Topics"],
        responses={
            200: OpenApiResponse(
                response=TopicSerializer,
                description="The requested topic.",
                examples=[OpenApiExample(name="Success", value=_TOPIC_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Create a topic",
        description=(
            "Creates a topic under a category, fixing the price paid for a "
            "course submitted under it - more specific than the category's "
            "own price, and preferred when set (see submit_course pricing "
            "rule).\n\n"
            "Called from the 'Create topic' action on the Topics screen.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** None beyond holding the Admin or Creator "
            "Reviewer role.\n\n"
            "**Important:** Editing `creator_price` later is not "
            "retroactive, mirroring Category - it only affects courses "
            "submitted afterwards."
        ),
        tags=["Courses — Topics"],
        request=TopicWriteSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "category": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
                    "name": "Django REST Framework",
                    "creator_price": "180.00",
                    "status": "ACTIVE",
                },
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=TopicSerializer,
                description="Topic created.",
                examples=[OpenApiExample(name="Success", value=_TOPIC_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    update=extend_schema(
        summary="Replace a topic",
        description=(
            "Overwrites a topic's details. Send the full object; prefer "
            "PATCH for routine edits.\n\n"
            "Called from the topic edit dialog when every field is being "
            "submitted.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The topic must exist.\n\n"
            "**Important:** None."
        ),
        tags=["Courses — Topics"],
        request=TopicWriteSerializer,
        responses={
            200: OpenApiResponse(
                response=TopicSerializer,
                description="Topic updated.",
                examples=[OpenApiExample(name="Success", value=_TOPIC_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    partial_update=extend_schema(
        summary="Update a topic",
        description=(
            "Updates only the fields supplied - the normal way to reprice a "
            "topic or open/close it to submissions.\n\n"
            "Called from the topic edit dialog and the status toggle on the "
            "Topics screen.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The topic must exist.\n\n"
            "**Important:** Setting `status` to `INACTIVE` stops new "
            "submissions but leaves courses already using the topic "
            "untouched."
        ),
        tags=["Courses — Topics"],
        request=TopicWriteSerializer,
        examples=[
            OpenApiExample(
                name="Reprice",
                request_only=True,
                value={"creator_price": "200.00"},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=TopicSerializer,
                description="Topic updated.",
                examples=[OpenApiExample(name="Success", value=_TOPIC_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    destroy=extend_schema(
        summary="Delete a topic",
        description=(
            "Deletes a topic outright. Unlike Category, there is no "
            "deletion-impact/strategy flow - a topic with courses attached "
            "is deleted along with the FK still pointing at it via the "
            "database's own cascade/protect behavior.\n\n"
            "Called from the delete action on the Topics screen.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The topic must exist.\n\n"
            "**Important:** To retire a topic without deleting it, PATCH "
            "`status` to `INACTIVE` instead."
        ),
        tags=["Courses — Topics"],
        responses={
            204: OpenApiResponse(description="Topic deleted."),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class TopicViewSet(ModelViewSet):
    """Admin/Creator-Reviewer-managed course topics nested under a Category.

    List/retrieve are open to any authenticated user - creators need to
    browse topics under a chosen category before creating a course. Mirrors
    CategoryViewSet exactly, including Reviewer write access for pricing.
    """

    queryset = Topic.objects.select_related("category").all()
    filterset_class = TopicFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["name", "creator_price", "created_datetime"]

    def get_serializer_class(self):
        if self.action in WRITE_ACTIONS:
            return TopicWriteSerializer
        return TopicSerializer

    def get_permissions(self):
        if self.action in WRITE_ACTIONS or self.action == "release_reservation":
            return [(IsAdminRole | IsCreatorReviewerRole)()]
        return super().get_permissions()

    @extend_schema(
        summary="Manually release a topic's reservation",
        description=(
            "Clears `reserved_by`/`reserved_until` on a topic, freeing it "
            "up for a new reservation request even if the reservation "
            "hasn't naturally expired yet. Exists for the case where a "
            "creator who reserved a topic goes inactive.\n\n"
            "Called from the 'Release reservation' action on the admin "
            "Topics screen.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** None beyond holding the Admin or Creator "
            "Reviewer role.\n\n"
            "**Important:** Idempotent - releasing a topic that isn't "
            "currently reserved is a harmless no-op, not an error."
        ),
        tags=["Courses — Topics"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=TopicSerializer,
                description="Topic with its reservation cleared.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            **_TOPIC_EXAMPLE,
                            "reserved_by": None,
                            "reserved_until": None,
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"], url_path="release-reservation")
    def release_reservation(self, request, pk=None):
        topic = topic_reservation_service.release_reservation(
            topic=self.get_object(), actor=request.user
        )
        return Response(TopicSerializer(topic).data)
