from django.urls import path

from api.production.views.admin_production_views import (
    ChannelMappingActivateView,
    ChannelMappingListCreateView,
    ChannelMappingPreviewView,
    CoursePackageView,
    DistributionPublishedView,
    ProductionRunCancelView,
    ProductionRunDetailView,
    ProductionRunListView,
    ProductionRunRetryView,
)

urlpatterns = [
    path("admin/production/runs/", ProductionRunListView.as_view(), name="production-run-list"),
    path("admin/production/runs/<uuid:run_id>/", ProductionRunDetailView.as_view(), name="production-run-detail"),
    path(
        "admin/production/runs/<uuid:run_id>/retry/",
        ProductionRunRetryView.as_view(),
        name="production-run-retry",
    ),
    path(
        "admin/production/runs/<uuid:run_id>/cancel/",
        ProductionRunCancelView.as_view(),
        name="production-run-cancel",
    ),
    path(
        "admin/production/channel-mappings/",
        ChannelMappingListCreateView.as_view(),
        name="production-channel-mapping-list",
    ),
    path(
        "admin/production/channel-mappings/<uuid:mapping_id>/activate/",
        ChannelMappingActivateView.as_view(),
        name="production-channel-mapping-activate",
    ),
    path(
        "admin/production/channel-mappings/<uuid:mapping_id>/preview/",
        ChannelMappingPreviewView.as_view(),
        name="production-channel-mapping-preview",
    ),
    path(
        "admin/production/courses/<uuid:course_id>/package/",
        CoursePackageView.as_view(),
        name="production-course-package",
    ),
    path(
        "admin/production/distributions/<uuid:distribution_id>/published/",
        DistributionPublishedView.as_view(),
        name="production-distribution-published",
    ),
]
