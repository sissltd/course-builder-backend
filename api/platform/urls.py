from django.urls import path

from api.platform.views import AdminOverviewView, PlatformSettingsView

urlpatterns = [
    path(
        "platform-settings/",
        PlatformSettingsView.as_view(),
        name="platform-settings",
    ),
    path(
        "admin/overview/",
        AdminOverviewView.as_view(),
        name="admin-overview",
    ),
]
