from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from api.authentication import views

urlpatterns = [
    path("auth/signup/", views.SignupView.as_view(), name="auth-signup"),
    path(
        "auth/reviewer/signup/",
        views.ReviewerSignupView.as_view(),
        name="auth-reviewer-signup",
    ),
    path(
        "auth/verify-email/", views.VerifyEmailView.as_view(), name="auth-verify-email"
    ),
    path(
        "auth/resend-verification/",
        views.ResendVerificationView.as_view(),
        name="auth-resend-verification",
    ),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path(
        "auth/reviewer/login/", views.LoginView.as_view(), name="auth-reviewer-login"
    ),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="auth-token-refresh"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path(
        "auth/forgot-password/",
        views.ForgotPasswordView.as_view(),
        name="auth-forgot-password",
    ),
    path(
        "auth/reset-password/",
        views.ResetPasswordView.as_view(),
        name="auth-reset-password",
    ),
]
