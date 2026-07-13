from django.urls import path

from api.onboarding.views import OnboardingView

urlpatterns = [
    path("users/me/onboarding/", OnboardingView.as_view(), name="user-onboarding"),
]
