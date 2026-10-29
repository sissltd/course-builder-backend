from django.urls import path

from api.achievements.views.admin_badge_views import (
    BadgeDeletionImpactView,
    BadgeDetailView,
    BadgeHolderDetailView,
    BadgeHolderListCreateView,
    BadgeListCreateView,
)
from api.achievements.views.creator_achievement_views import (
    CreatorAchievementListView,
)

urlpatterns = [
    path(
        "admin/achievements/badges/",
        BadgeListCreateView.as_view(),
        name="admin-badge-list",
    ),
    path(
        "admin/achievements/badges/<uuid:badge_id>/",
        BadgeDetailView.as_view(),
        name="admin-badge-detail",
    ),
    path(
        "admin/achievements/badges/<uuid:badge_id>/deletion-impact/",
        BadgeDeletionImpactView.as_view(),
        name="admin-badge-deletion-impact",
    ),
    path(
        "admin/achievements/badges/<uuid:badge_id>/holders/",
        BadgeHolderListCreateView.as_view(),
        name="admin-badge-holders",
    ),
    path(
        "admin/achievements/badges/<uuid:badge_id>/holders/<uuid:creator_id>/",
        BadgeHolderDetailView.as_view(),
        name="admin-badge-holder-detail",
    ),
    path(
        "creator/achievements/",
        CreatorAchievementListView.as_view(),
        name="creator-achievements",
    ),
]
