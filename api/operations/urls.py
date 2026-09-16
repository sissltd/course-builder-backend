from django.urls import path

from api.operations.views import (
    AdminAnalyticsView,
    MieRecommendationApproveView,
    MieRecommendationBulkDecisionView,
    MieRecommendationRejectView,
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
        "admin/mie-recommendations/decisions/",
        MieRecommendationBulkDecisionView.as_view(),
        name="admin-mie-recommendation-decisions",
    ),
    path(
        "admin/mie-recommendations/<uuid:id>/approve/",
        MieRecommendationApproveView.as_view(),
        name="admin-mie-recommendation-approve",
    ),
    path(
        "admin/mie-recommendations/<uuid:id>/reject/",
        MieRecommendationRejectView.as_view(),
        name="admin-mie-recommendation-reject",
    ),
    path(
        "admin/pipeline/",
        PipelineOverviewView.as_view(),
        name="admin-pipeline",
    ),
]
