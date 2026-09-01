from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
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
from api.users.permissions import IsAdminRole


class AdminTopicReservationRequestViewSet(ReadOnlyModelViewSet):
    """Admin dashboard queue for proposed-topic reservation requests."""

    permission_classes = [IsAdminRole]
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


class ActiveTopicReservationViewSet(ReadOnlyModelViewSet):
    """Currently active topic reservations for the Admin dashboard."""

    permission_classes = [IsAdminRole]
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
