from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated

from api.users.serializers import MeSerializer


class MeView(RetrieveAPIView):
    """Return the current authenticated user's profile."""

    permission_classes = [IsAuthenticated]
    serializer_class = MeSerializer

    def get_object(self):
        return self.request.user
