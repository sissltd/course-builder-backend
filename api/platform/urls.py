from django.urls import path

from api.platform.views import PlatformSettingsView

urlpatterns = [
    path(
        "platform-settings/",
        PlatformSettingsView.as_view(),
        name="platform-settings",
    ),
]
