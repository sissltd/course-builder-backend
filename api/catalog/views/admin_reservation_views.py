from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import filters as drf_filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from api.catalog.filters import (
    ActiveTopicReservationFilter,
    AdminReservationRequestFilter,
)
from api.catalog.models import Topic, TopicReservationRequest
from api.catalog.serializers import (
    ActiveTopicReservationSerializer,
    AdminTopicReservationRequestSerializer,
    TopicReservationRejectSerializer,
)
from api.catalog.services import topic_reservation_service
from api.authorization import codenames
from api.authorization.permissions import Perm
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_TOPIC_REQUEST_EXAMPLE = {
    "id": "f6a7b8c9-d0e1-4f2a-3b4c-5d6e7f8a9b0c",
    "name": "Django REST Framework",
    "category": {
        "id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
        "name": "Software Engineering",
    },
    "topic": None,
    "status": "PENDING",
    "rejection_reason": None,
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
        summary="List reservation requests",
        description=(
            "Returns all proposed topic requests for the administrative review "
            "queue, newest first.\n\n"
            "Call this when the Admin reservation queue opens; filter by status, "
            "category, requester, search text, or dates as needed.\n\n"
            "**Auth:** The `catalog.view_topic_queue` permission — Admin, Approver "
            "and Super Admin by default.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** A request's `topic` is null until approval. Results "
            "are paginated."
        ),
        tags=["Admin — Reservation"],
        responses={
            200: OpenApiResponse(
                response=AdminTopicReservationRequestSerializer(many=True),
                examples=[OpenApiExample("Success", value=[_TOPIC_REQUEST_EXAMPLE])],
            )
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a reservation request",
        description=(
            "Returns one proposed topic request for the administrative review "
            "panel.\n\n"
            "Call this after selecting a request from the queue.\n\n"
            "**Auth:** The `catalog.view_topic_queue` permission — Admin, Approver "
            "and Super Admin by default.\n\n"
            "**Prerequisites:** The request must exist.\n\n"
            "**Important:** Requester and reviewer identity are included for the "
            "audit trail."
        ),
        tags=["Admin — Reservation"],
        responses={
            200: OpenApiResponse(
                response=AdminTopicReservationRequestSerializer,
                examples=[OpenApiExample("Success", value=_TOPIC_REQUEST_EXAMPLE)],
            )
        },
    ),
    approve=extend_schema(
        summary="Approve a topic request",
        description=(
            "Approves a Pending topic request, creates the topic under its "
            "category, and reserves it for the requesting creator.\n\n"
            "Call this after reviewing the proposed name and category.\n\n"
            "**Auth:** The `catalog.view_topic_queue` permission — Admin, Approver "
            "and Super Admin by default.\n\n"
            "**Prerequisites:** The request must be Pending and the topic name "
            "must be unique within its category.\n\n"
            "**Important:** The new topic inherits the category's beginner "
            "creator price; no price is accepted in this request."
        ),
        tags=["Admin — Reservation"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=AdminTopicReservationRequestSerializer,
                examples=[
                    OpenApiExample(
                        "Approved", value={**_TOPIC_REQUEST_EXAMPLE, "status": "APPROVED"}
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    reject=extend_schema(
        summary="Reject a topic request",
        description=(
            "Rejects a Pending topic request and retains it in the review history.\n\n"
            "Call this after deciding the proposed topic should not be added.\n\n"
            "**Auth:** The `catalog.view_topic_queue` permission — Admin, Approver "
            "and Super Admin by default.\n\n"
            "**Prerequisites:** The request must be Pending.\n\n"
            "**Important:** The optional reason is retained and no topic is created."
        ),
        tags=["Admin — Reservation"],
        request=TopicReservationRejectSerializer,
        examples=[OpenApiExample("Reject", request_only=True, value={"reason": "Duplicate topic."})],
        responses={
            200: OpenApiResponse(
                response=AdminTopicReservationRequestSerializer,
                examples=[
                    OpenApiExample(
                        "Rejected", value={**_TOPIC_REQUEST_EXAMPLE, "status": "REJECTED"}
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class AdminTopicReservationRequestViewSet(ReadOnlyModelViewSet):
    """Admin dashboard queue for proposed-topic reservation requests."""

    permission_classes = [Perm(codenames.CATALOG_VIEW_TOPIC_QUEUE)]
    serializer_class = AdminTopicReservationRequestSerializer
    filterset_class = AdminReservationRequestFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["created_datetime", "reviewed_at", "name", "status"]
    ordering = ["-created_datetime"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TopicReservationRequest.objects.none()
        return TopicReservationRequest.objects.select_related(
            "requested_by",
            "reviewed_by",
            "category",
            "topic",
            "topic__category",
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        reservation_request = topic_reservation_service.approve_request(
            request=self.get_object(), actor=request.user
        )
        return Response(self.get_serializer(reservation_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        serializer = TopicReservationRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reservation_request = topic_reservation_service.reject_request(
            request=self.get_object(),
            actor=request.user,
            reason=serializer.validated_data["reason"],
        )
        return Response(self.get_serializer(reservation_request).data)


@extend_schema_view(
    list=extend_schema(
        summary="List all topic requests", tags=["Admin — Topic Requests"]
    ),
    retrieve=extend_schema(
        summary="Retrieve a topic request", tags=["Admin — Topic Requests"]
    ),
    approve=extend_schema(tags=["Admin — Topic Requests"]),
    reject=extend_schema(tags=["Admin — Topic Requests"]),
)
class AdminWriterTopicRequestViewSet(AdminTopicReservationRequestViewSet):
    """Admin Writer queue for proposed topic requests."""

    permission_classes = [Perm(codenames.CATALOG_MANAGE_CATEGORIES)]


@extend_schema_view(
    list=extend_schema(
        summary="List active reservations", tags=["Admin — Reservation"]
    ),
    retrieve=extend_schema(
        summary="Retrieve an active reservation", tags=["Admin — Reservation"]
    ),
    release=extend_schema(tags=["Admin — Reservation"]),
)
class ActiveTopicReservationViewSet(ReadOnlyModelViewSet):
    """Currently active topic reservations for the Admin dashboard."""

    permission_classes = [Perm(codenames.CATALOG_VIEW_TOPIC_QUEUE)]
    serializer_class = ActiveTopicReservationSerializer
    filterset_class = ActiveTopicReservationFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["name", "reserved_until", "created_datetime"]
    ordering = ["reserved_until", "name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Topic.objects.none()
        return Topic.objects.select_related("category", "reserved_by").filter(
            reserved_by__isnull=False,
            reserved_until__gte=timezone.localdate(),
        )

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        topic = topic_reservation_service.release_reservation(
            topic=self.get_object(), actor=request.user
        )
        return Response(self.get_serializer(topic).data)
