from django.urls import path

from api.platform.views import (
    AdminOverviewView,
    CreatorOverviewView,
    PlatformSettingsView,
    ReviewerOverviewView,
)

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
    path(
        "creator/overview/",
        CreatorOverviewView.as_view(),
        name="creator-overview",
    ),
    path(
        "reviewer/overview/",
        ReviewerOverviewView.as_view(),
        name="reviewer-overview",
    ),
]
