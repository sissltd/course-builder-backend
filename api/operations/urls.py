from django.urls import path

from api.operations.views import (
    AdminAnalyticsView,
    MieRecommendationsView,
    PipelineOverviewView,
    SystemHealthView,
)

urlpatterns = [
    path("admin/analytics/", AdminAnalyticsView.as_view(), name="admin-analytics"),
    path(
        "admin/system-health/",
        SystemHealthView.as_view(),
        name="admin-system-health",
    ),
    path(
        "admin/mie-recommendations/",
        MieRecommendationsView.as_view(),
        name="admin-mie-recommendations",
    ),
    path(
        "admin/pipeline/",
        PipelineOverviewView.as_view(),
        name="admin-pipeline",
    ),
]
