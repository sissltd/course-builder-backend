from django.urls import path

from api.users.views import (
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
]
