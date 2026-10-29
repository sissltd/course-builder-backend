from django.urls import path

from api.users.views.account_admin_views import (
    StaffEraseView,
    StaffSendPasswordResetView,
    TeamInvitationView,
    TeamsEraseView,
    TeamsSendPasswordResetView,
)

from api.users.views import (
    AdminUserActivityLogListView,
    KYCReviewViewSet,
    KYCVerificationView,
    LivenessAvatarSettingView,
    LivenessVerificationView,
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
    path("users/me/kyc/liveness/", LivenessVerificationView.as_view(), name="user-me-liveness"),
    path(
        "users/kyc/set-avatar/<uuid:user_id>/",
        LivenessAvatarSettingView.as_view(),
        name="user-me-set-avatar",
    ),
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
        "users/kyc-review/<uuid:pk>/flag/",
        KYCReviewViewSet.as_view({"post": "flag"}),
        name="kyc-review-flag",
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
        "users/admin/<uuid:pk>/assign-track/",
        UserAdminViewSet.as_view({"post": "assign_track"}),
        name="user-admin-assign-track",
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
    path(
        "users/admin/invitations/",
        TeamInvitationView.as_view(),
        name="user-admin-invite",
    ),
    path(
        "users/admin/<uuid:pk>/send-password-reset/",
        TeamsSendPasswordResetView.as_view(),
        name="user-admin-send-password-reset",
    ),
    path("users/admin/<uuid:pk>/erase/", TeamsEraseView.as_view(), name="user-admin-erase"),
    path(
        "auth/staff/<uuid:pk>/send-password-reset/",
        StaffSendPasswordResetView.as_view(),
        name="auth-staff-send-password-reset",
    ),
    path("auth/staff/<uuid:pk>/erase/", StaffEraseView.as_view(), name="auth-staff-erase"),
]
