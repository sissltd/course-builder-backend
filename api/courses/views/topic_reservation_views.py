from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from api.courses.models import TopicReservationRequest
from api.courses.serializers import (
    TopicReservationRejectSerializer,
    TopicReservationRequestCreateSerializer,
    TopicReservationRequestSerializer,
)
from api.courses.services import topic_reservation_service
from api.users.permissions import IsAdminRole, IsCourseCreatorRole, IsCreatorReviewerRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

MANAGE_ACTIONS = {"approve", "reject"}

_CATEGORY_MINI_EXAMPLE = {
    "id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
    "name": "Software Engineering",
}

_TOPIC_EXAMPLE = {
    "id": "e5f6a7b8-c9d0-4e1f-2a3b-4c5d6e7f8a9b",
    "category": _CATEGORY_MINI_EXAMPLE,
    "name": "Django REST Framework",
    "creator_price": "180.00",
    "status": "ACTIVE",
    "reserved_by": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    "reserved_until": "2026-08-19",
    "is_currently_reserved": True,
    "created_datetime": "2026-08-19T11:00:00.000Z",
    "updated_datetime": "2026-08-19T11:00:00.000Z",
}

_RESERVATION_EXAMPLE = {
    "id": "f6a7b8c9-d0e1-4f2a-3b4c-5d6e7f8a9b0c",
    "name": "Django REST Framework",
    "category": _CATEGORY_MINI_EXAMPLE,
    "topic": None,
    "status": "PENDING",
    "rejection_reason": None,
    "reviewed_at": None,
    "created_datetime": "2026-07-20T11:00:00.000Z",
}


@extend_schema_view(
    list=extend_schema(
        summary="List topic requests",
        description=(
            "Returns the caller's own requests for a new topic, or every "
            "request on the platform for an Admin/Creator Reviewer (the "
            "review queue for BR-007 topic requests).\n\n"
            "Called when the My Requests screen loads for a creator, or the "
            "topic-requests queue for Admin/Reviewer.\n\n"
            "**Auth:** Course Creator/Writer (own requests), or Admin/"
            "Creator Reviewer/Verifier (every request).\n\n"
            "**Prerequisites:** None beyond holding one of those roles.\n\n"
            "**Important:** `topic` is null until the request is approved - "
            "this endpoint is for proposing a topic that doesn't exist yet. "
            "Reserving an *existing* topic instead happens automatically "
            "when a creator starts a Draft course with it selected (see "
            "`POST /api/v1/courses/`), no request needed. Results are "
            "paginated."
        ),
        tags=["Courses — Topic Reservations"],
        responses={
            200: OpenApiResponse(
                response=TopicReservationRequestSerializer(many=True),
                description="Reservation requests.",
                examples=[OpenApiExample(name="Success", value=[_RESERVATION_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a topic request",
        description=(
            "Returns a single topic request.\n\n"
            "Called when opening a request's detail view - the Figma detail "
            "panel shows `rejection_reason` for a Rejected request.\n\n"
            "**Auth:** The requesting creator, or Admin/Creator Reviewer/"
            "Verifier.\n\n"
            "**Prerequisites:** The request must exist and be visible to "
            "the caller.\n\n"
            "**Important:** A creator requesting someone else's request "
            "gets 404, not 403 - existence isn't leaked."
        ),
        tags=["Courses — Topic Reservations"],
        responses={
            200: OpenApiResponse(
                response=TopicReservationRequestSerializer,
                description="The requested reservation request.",
                examples=[OpenApiExample(name="Success", value=_RESERVATION_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    update=extend_schema(
        summary="Replace a topic request",
        description=(
            "Full-object update on a topic request. Not part of the normal "
            "workflow (a request is decided via approve/reject, not "
            "edited) - exposed only because the viewset shares ModelViewSet "
            "CRUD.\n\n"
            "**Auth:** The requesting creator, or Admin/Creator Reviewer/"
            "Verifier.\n\n"
            "**Prerequisites:** The request must exist and be visible to "
            "the caller.\n\n"
            "**Important:** Every field on `TopicReservationRequestSerializer` "
            "is read-only, so a PUT body has nothing to change."
        ),
        tags=["Courses — Topic Reservations"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=TopicReservationRequestSerializer,
                description="Reservation request (unchanged).",
                examples=[OpenApiExample(name="Success", value=_RESERVATION_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    partial_update=extend_schema(
        summary="Update a topic request",
        description=(
            "Partial-object update on a topic request. Not part of the "
            "normal workflow (a request is decided via approve/reject, not "
            "edited) - exposed only because the viewset shares "
            "ModelViewSet CRUD.\n\n"
            "**Auth:** The requesting creator, or Admin/Creator Reviewer/"
            "Verifier.\n\n"
            "**Prerequisites:** The request must exist and be visible to "
            "the caller.\n\n"
            "**Important:** Every field on `TopicReservationRequestSerializer` "
            "is read-only, so a PATCH body has nothing to change."
        ),
        tags=["Courses — Topic Reservations"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=TopicReservationRequestSerializer,
                description="Reservation request (unchanged).",
                examples=[OpenApiExample(name="Success", value=_RESERVATION_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    destroy=extend_schema(
        summary="Delete a topic request",
        description=(
            "Deletes a topic request outright. Not part of the normal "
            "workflow (a request is decided via approve/reject, which "
            "preserves history) - exposed only because the viewset shares "
            "ModelViewSet CRUD.\n\n"
            "**Auth:** The requesting creator, or Admin/Creator Reviewer/"
            "Verifier.\n\n"
            "**Prerequisites:** The request must exist and be visible to "
            "the caller.\n\n"
            "**Important:** Deleting an Approved request does not delete or "
            "release the Topic it created - use `release-reservation` on "
            "the topic for that."
        ),
        tags=["Courses — Topic Reservations"],
        responses={
            204: OpenApiResponse(description="Request deleted."),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Request a new topic",
        description=(
            "Submits a Pending request for a brand-new topic under a "
            "category (PRD BR-007), and notifies Admins/Creator Reviewers "
            "in-app. This is the Figma 'Request topic' flow - the creator "
            "types a topic name and picks a category, not an existing "
            "topic.\n\n"
            "Called from the 'Request topic' action on the Reservation "
            "screen when a creator wants a topic that isn't in the catalog "
            "yet.\n\n"
            "**Auth:** Course Creator or Writer.\n\n"
            "**Prerequisites:** None beyond holding the Course Creator/"
            "Writer role.\n\n"
            "**Important:** No automatic duplicate-name check runs at "
            "submit time - the request always starts Pending. Whether "
            "`name` already matches an existing topic is a judgment call "
            "the reviewing admin/reviewer makes, reflected in "
            "`rejection_reason` if they reject it. To reserve a topic that "
            "**already exists**, don't use this endpoint at all - just "
            "start a Draft course with that topic selected via "
            "`POST /api/v1/courses/`, which reserves it automatically."
        ),
        tags=["Courses — Topic Reservations"],
        request=TopicReservationRequestCreateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "name": "Fundamentals of Programming",
                    "category": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
                },
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=TopicReservationRequestSerializer,
                description="Topic request created as Pending.",
                examples=[OpenApiExample(name="Success", value=_RESERVATION_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class TopicReservationRequestViewSet(ModelViewSet):
    """A creator's requests for a brand-new Topic (PRD BR-007), plus
    Admin/Reviewer approve/reject. Approving both creates the Topic and
    reserves it to the requester in one step. Reserving an *existing* topic
    doesn't go through this viewset at all - see
    course_service.create_draft_course's automatic reservation. This is now
    the only category/topic-adjacent request flow a Course Creator has -
    proposing a brand-new Category is no longer possible via the API since
    Admins/Writers create categories directly."""

    permission_classes = [IsCourseCreatorRole | IsAdminRole | IsCreatorReviewerRole]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TopicReservationRequest.objects.none()

        queryset = TopicReservationRequest.objects.select_related(
            "requested_by", "category", "topic", "topic__category"
        )
        if self.request.user.is_superuser or self.request.user.role in (
            IsAdminRole.allowed_roles + IsCreatorReviewerRole.allowed_roles
        ):
            return queryset
        return queryset.filter(requested_by=self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return TopicReservationRequestCreateSerializer
        return TopicReservationRequestSerializer

    def get_permissions(self):
        if self.action in MANAGE_ACTIONS:
            return [(IsAdminRole | IsCreatorReviewerRole)()]
        return super().get_permissions()

    @extend_schema(
        summary="Approve a topic request",
        description=(
            "Approves a Pending request: creates the real Topic under the "
            "requested category (price inherited from the category), and "
            "reserves it to the requesting creator until "
            "`topic_reservation_expiry_days` (platform setting) from "
            "today - in one step, since the whole point of the request was "
            "to claim the topic.\n\n"
            "Called from the 'Approve' action on the topic-requests "
            "queue.\n\n"
            "**Auth:** Admin or Creator Reviewer/Verifier.\n\n"
            "**Prerequisites:** The request must be `PENDING`; `name` must "
            "not already be taken by another topic in the same "
            "`category`.\n\n"
            "**Important:** Unlike creating a Topic directly via "
            "`TopicViewSet`, no separate `creator_price` input exists here "
            "- it's always copied from the category at approval time."
        ),
        tags=["Courses — Topic Reservations"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=TopicReservationRequestSerializer,
                description="Request approved, topic created and reserved.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            **_RESERVATION_EXAMPLE,
                            "status": "APPROVED",
                            "topic": _TOPIC_EXAMPLE,
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                description="The request isn't Pending, or the name is already taken in this category.",
                examples=[
                    OpenApiExample(
                        name="Wrong status",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "Request cannot be approved from "
                                        "status 'APPROVED'."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Duplicate name",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "A topic with this name already "
                                        "exists in this category."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        reservation_request = topic_reservation_service.approve_request(
            request=self.get_object(), actor=request.user
        )
        return Response(TopicReservationRequestSerializer(reservation_request).data)

    @extend_schema(
        summary="Reject a topic request",
        description=(
            "Rejects a Pending request with an optional free-text reason - "
            "e.g. the Figma example 'Your topic was rejected because this "
            "topic already exists in our database'. No email is sent - "
            "only approvals notify the requester.\n\n"
            "Called from the 'Reject' action on the topic-requests "
            "queue.\n\n"
            "**Auth:** Admin or Creator Reviewer/Verifier.\n\n"
            "**Prerequisites:** The request must be `PENDING`.\n\n"
            "**Important:** No Topic is created - rejecting never reserves "
            "or releases anything. Whether the name duplicates an existing "
            "topic is the reviewer's own judgment call; nothing on the "
            "backend checks this automatically."
        ),
        tags=["Courses — Topic Reservations"],
        request=TopicReservationRejectSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "reason": (
                        "Your topic was rejected because this topic "
                        "already exists in our database."
                    )
                },
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=TopicReservationRequestSerializer,
                description="Request rejected.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            **_RESERVATION_EXAMPLE,
                            "status": "REJECTED",
                            "rejection_reason": (
                                "Your topic was rejected because this "
                                "topic already exists in our database."
                            ),
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                description="The request isn't Pending.",
                examples=[
                    OpenApiExample(
                        name="Wrong status",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "Request cannot be rejected from "
                                        "status 'REJECTED'."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        serializer = TopicReservationRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reservation_request = topic_reservation_service.reject_request(
            request=self.get_object(),
            actor=request.user,
            reason=serializer.validated_data["reason"],
        )
        return Response(TopicReservationRequestSerializer(reservation_request).data)
