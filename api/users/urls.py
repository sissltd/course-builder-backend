from django.urls import path

from api.users.views import MeView, ReviewerAvailabilityView, UserActivityLogListView

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
]
