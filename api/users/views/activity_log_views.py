from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters as drf_filters
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated

from api.users.filters import UserActivityLogFilter
from api.users.models import UserActivityLog
from api.users.serializers.activity_log_serializer import UserActivityLogSerializer


class UserActivityLogListView(ListAPIView):
    """List the current authenticated user's own activity log entries.

    Filterable by ?category= (matches the frontend's tab filters) and/or
    ?action=. Ordered newest-first (the model's own default ordering).
    """

    permission_classes = [IsAuthenticated]
    serializer_class = UserActivityLogSerializer
    filterset_class = UserActivityLogFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["activity_datetime"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return UserActivityLog.objects.none()
        return UserActivityLog.objects.filter(
            user=self.request.user
        ).select_related("actor_user")
