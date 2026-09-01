from django.urls import path

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
    path("auth/reviewer/login/", views.LoginView.as_view(), name="auth-reviewer-login"),
    path(
        "auth/token/refresh/",
        views.TokenRefreshView.as_view(),
        name="auth-token-refresh",
    ),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/logout-all/", views.LogoutAllView.as_view(), name="auth-logout-all"),
    path(
        "auth/sessions/",
        views.UserSessionListView.as_view(),
        name="auth-session-list",
    ),
    path(
        "auth/sessions/<uuid:pk>/",
        views.UserSessionRevokeView.as_view(),
        name="auth-session-revoke",
    ),
    path("auth/mfa/enroll/", views.MFAEnrollView.as_view(), name="auth-mfa-enroll"),
    path(
        "auth/mfa/enroll/confirm/",
        views.MFAEnrollConfirmView.as_view(),
        name="auth-mfa-enroll-confirm",
    ),
    path("auth/mfa/verify/", views.MFAVerifyView.as_view(), name="auth-mfa-verify"),
    path(
        "auth/mfa/recovery-codes/regenerate/",
        views.MFARecoveryCodesRegenerateView.as_view(),
        name="auth-mfa-recovery-codes-regenerate",
    ),
    path("auth/mfa/disable/", views.MFADisableView.as_view(), name="auth-mfa-disable"),
    path(
        "auth/mfa/admin-reset/<uuid:user_id>/",
        views.MFAAdminResetView.as_view(),
        name="auth-mfa-admin-reset",
    ),
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
    path(
        "auth/change-password/",
        views.ChangePasswordView.as_view(),
        name="auth-change-password",
    ),
    path(
        "auth/change-email/",
        views.ChangeEmailRequestView.as_view(),
        name="auth-change-email-request",
    ),
    path(
        "auth/change-email/confirm/",
        views.ChangeEmailConfirmView.as_view(),
        name="auth-change-email-confirm",
    ),
    path(
        "auth/superadmin/bootstrap/",
        views.SuperAdminBootstrapView.as_view(),
        name="auth-superadmin-bootstrap",
    ),
    path(
        "auth/staff/",
        views.StaffListView.as_view(),
        name="auth-staff-list",
    ),
    path(
        "auth/staff/invitations/",
        views.InviteStaffView.as_view(),
        name="auth-staff-invite",
    ),
    path(
        "auth/staff/invitations/accept/",
        views.AcceptStaffInvitationView.as_view(),
        name="auth-staff-invitation-accept",
    ),
    path(
        "auth/staff/<uuid:pk>/",
        views.StaffDetailView.as_view(),
        name="auth-staff-detail",
    ),
    path(
        "auth/staff/<uuid:pk>/revoke/",
        views.RevokeStaffView.as_view(),
        name="auth-staff-revoke",
    ),
    path(
        "auth/staff/<uuid:pk>/reactivate/",
        views.ReactivateStaffView.as_view(),
        name="auth-staff-reactivate",
    ),
]
