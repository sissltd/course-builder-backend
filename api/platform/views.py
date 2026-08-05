from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.platform.serializers import (
    PlatformSettingsSerializer,
    PlatformSettingsUpdateSerializer,
)
from api.platform.services import platform_settings_service
from api.users.permissions import IsAdminOrSuperAdminRole


class PlatformSettingsView(APIView):
    """GET/PATCH the platform's singleton settings row.

    GET is open to any authenticated user - the frontend needs to display
    live thresholds (e.g. "4-12 modules required") without hardcoding them.
    PATCH is Admin/Super Admin only (excludes Approver - these thresholds
    affect every course on the platform, a higher-stakes action than
    ordinary admin-tier work).
    """

    serializer_class = PlatformSettingsUpdateSerializer  # for schema generation only

    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsAdminOrSuperAdminRole()]
        return [IsAuthenticated()]

    def get(self, request):
        settings_row = platform_settings_service.get_settings()
        return Response(PlatformSettingsSerializer(settings_row).data)

    def patch(self, request):
        serializer = PlatformSettingsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        settings_row = platform_settings_service.update_settings(
            **serializer.validated_data
        )
        return Response(PlatformSettingsSerializer(settings_row).data)
