from django.urls import path

from api.notification.views import (
    NotificationListView,
    NotificationMarkAllReadView,
    NotificationPreferenceView,
    NotificationReadToggleView,
    NotificationStreamView,
    NotificationUnreadCountView,
)

urlpatterns = [
    path(
        "users/me/notification-preferences/",
        NotificationPreferenceView.as_view(),
        name="user-notification-preferences",
    ),
    path(
        "users/me/notifications/",
        NotificationListView.as_view(),
        name="user-notifications",
    ),
    path(
        "users/me/notifications/streamed-notifications/",
        NotificationStreamView.as_view(),
        name="user-streamed-notifications",
    ),
    path(
        "users/me/notifications/toggle-read/",
        NotificationReadToggleView.as_view(),
        name="user-toggle-read-notifications",
    ),
    path(
        "users/me/notifications/unread-count/",
        NotificationUnreadCountView.as_view(),
        name="user-notifications-unread-count",
    ),
    path(
        "users/me/notifications/mark-all-read/",
        NotificationMarkAllReadView.as_view(),
        name="user-notifications-mark-all-read",
    ),
]
