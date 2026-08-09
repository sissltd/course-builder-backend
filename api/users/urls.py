from django.urls import path

from api.users.views import (
    AdminUserActivityLogListView,
    KYCReviewViewSet,
    KYCVerificationView,
    MeView,
    QueueBehaviourPreferenceView,
    ReviewerAvailabilityView,
    UserActivityLogExportView,
    UserActivityLogListView,
    UserAdminViewSet,
)

urlpatterns = [
    path("users/me/", MeView.as_view(), name="user-me"),
    path(
        "users/me/availability/",
        ReviewerAvailabilityView.as_view(),
        name="user-reviewer-availability",
    ),
    path(
        "users/me/queue-preferences/",
        QueueBehaviourPreferenceView.as_view(),
        name="user-queue-preferences",
    ),
    path(
        "users/me/activity-log/",
        UserActivityLogListView.as_view(),
        name="user-activity-log",
    ),
    path(
        "users/me/activity-log/export/",
        UserActivityLogExportView.as_view(),
        name="user-activity-log-export",
    ),
    path("users/me/kyc/", KYCVerificationView.as_view(), name="user-kyc"),
    path(
        "users/kyc-review/",
        KYCReviewViewSet.as_view({"get": "list"}),
        name="kyc-review-list",
    ),
    path(
        "users/kyc-review/<uuid:pk>/",
        KYCReviewViewSet.as_view({"get": "retrieve"}),
        name="kyc-review-detail",
    ),
    path(
        "users/kyc-review/<uuid:pk>/approve/",
        KYCReviewViewSet.as_view({"post": "approve"}),
        name="kyc-review-approve",
    ),
    path(
        "users/kyc-review/<uuid:pk>/reject/",
        KYCReviewViewSet.as_view({"post": "reject"}),
        name="kyc-review-reject",
    ),
    path(
        "users/admin/",
        UserAdminViewSet.as_view({"get": "list"}),
        name="user-admin-list",
    ),
    path(
        "users/admin/<uuid:pk>/",
        UserAdminViewSet.as_view({"get": "retrieve"}),
        name="user-admin-detail",
    ),
    path(
        "users/admin/<uuid:pk>/suspend/",
        UserAdminViewSet.as_view({"post": "suspend"}),
        name="user-admin-suspend",
    ),
    path(
        "users/admin/<uuid:pk>/deactivate/",
        UserAdminViewSet.as_view({"post": "deactivate"}),
        name="user-admin-deactivate",
    ),
    path(
        "users/admin/<uuid:pk>/reinstate/",
        UserAdminViewSet.as_view({"post": "reinstate"}),
        name="user-admin-reinstate",
    ),
    path(
        "users/activity-log/",
        AdminUserActivityLogListView.as_view(),
        name="admin-activity-log",
    ),
]
