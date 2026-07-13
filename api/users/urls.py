from django.urls import path

from api.users.views import MeView

urlpatterns = [
    path("users/me/", MeView.as_view(), name="user-me"),
]
