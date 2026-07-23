from django.urls import path

from api.users.views import (
    KYCReviewViewSet,
    KYCVerificationView,
    MeView,
    ReviewerAvailabilityView,
    UserActivityLogListView,
)

urlpatterns = [
    path("users/me/", MeView.as_view(), name="user-me"),
    path(
        "users/me/availability/",
        ReviewerAvailabilityView.as_view(),
        name="user-reviewer-availability",
    ),
    path(
        "users/me/activity-log/",
        UserActivityLogListView.as_view(),
        name="user-activity-log",
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
]
