from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from api.courses.models import TopicReservationRequest
from api.courses.serializers import (
    TopicReservationRequestCreateSerializer,
    TopicReservationRequestSerializer,
)
from api.courses.services import topic_reservation_service
from api.users.permissions import IsAdminRole, IsCourseCreatorRole, IsCreatorReviewerRole

MANAGE_ACTIONS = {"approve", "reject"}


class TopicReservationRequestViewSet(ModelViewSet):
    """A creator's requests to reserve a Topic (PRD BR-007), plus
    Admin/Reviewer approve/reject. Mirrors CategoryRequestViewSet exactly."""

    permission_classes = [IsCourseCreatorRole | IsAdminRole | IsCreatorReviewerRole]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TopicReservationRequest.objects.none()

        queryset = TopicReservationRequest.objects.select_related(
            "requested_by", "topic", "topic__category"
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

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        reservation_request = topic_reservation_service.approve_request(
            request=self.get_object(), actor=request.user
        )
        return Response(TopicReservationRequestSerializer(reservation_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        reservation_request = topic_reservation_service.reject_request(
            request=self.get_object(), actor=request.user
        )
        return Response(TopicReservationRequestSerializer(reservation_request).data)
