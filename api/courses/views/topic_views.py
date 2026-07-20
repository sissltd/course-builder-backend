from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters as drf_filters
from rest_framework.viewsets import ModelViewSet

from api.courses.filters import TopicFilter
from api.courses.models import Topic
from api.courses.serializers import TopicSerializer, TopicWriteSerializer
from api.users.permissions import IsAdminRole

WRITE_ACTIONS = {"create", "update", "partial_update", "destroy"}


class TopicViewSet(ModelViewSet):
    """Admin-managed course topics nested under a Category.

    List/retrieve are open to any authenticated user - creators need to
    browse topics under a chosen category before creating a course. Mirrors
    CategoryViewSet exactly.
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
        if self.action in WRITE_ACTIONS:
            return [IsAdminRole()]
        return super().get_permissions()
