from django.urls import path

from api.notification.views import NotificationPreferenceView

urlpatterns = [
    path(
        "users/me/notification-preferences/",
        NotificationPreferenceView.as_view(),
        name="user-notification-preferences",
    ),
]
