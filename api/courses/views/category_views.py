from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters as drf_filters
from rest_framework.viewsets import ModelViewSet

from api.courses.filters import CategoryFilter
from api.courses.models import Category
from api.courses.serializers import CategorySerializer, CategoryWriteSerializer
from api.users.permissions import IsAdminRole, IsCreatorReviewerRole

WRITE_ACTIONS = {"create", "update", "partial_update", "destroy"}


class CategoryViewSet(ModelViewSet):
    """Admin-managed course categories (SCCS PRD Section 7).

    List/retrieve are open to any authenticated user - creators need to browse
    categories to pick one (US-101). Create/update/delete are Admin or
    Creator Reviewer - Reviewers need write access to manage pricing.
    """

    queryset = Category.objects.all()
    filterset_class = CategoryFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["name", "creator_price", "created_datetime"]

    def get_serializer_class(self):
        if self.action in WRITE_ACTIONS:
            return CategoryWriteSerializer
        return CategorySerializer

    def get_permissions(self):
        if self.action in WRITE_ACTIONS:
            return [(IsAdminRole | IsCreatorReviewerRole)()]
        return super().get_permissions()
