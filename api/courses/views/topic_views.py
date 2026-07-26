from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters as drf_filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from api.courses.filters import TopicFilter
from api.courses.models import Topic
from api.courses.serializers import TopicSerializer, TopicWriteSerializer
from api.courses.services import topic_reservation_service
from api.users.permissions import IsAdminRole, IsCreatorReviewerRole

WRITE_ACTIONS = {"create", "update", "partial_update", "destroy"}


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

    @action(detail=True, methods=["post"], url_path="release-reservation")
    def release_reservation(self, request, pk=None):
        topic = topic_reservation_service.release_reservation(
            topic=self.get_object(), actor=request.user
        )
        return Response(TopicSerializer(topic).data)
