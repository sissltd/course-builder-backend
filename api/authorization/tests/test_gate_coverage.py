"""Every endpoint is gated by a stored permission, unless it is listed as self-service.

A new admin endpoint that forgets `Perm(...)` would be reachable by any
signed-in user and invisible to the Roles & Permissions screen. This fails
until the endpoint either checks a permission or is added below, with the
reason it needs none.
"""

from django.test import SimpleTestCase

from api.authorization.gate_coverage import ungated_handlers

#: Handlers deliberately open to anyone signed in (the caller's own account,
#: notifications, uploads, public catalog reads), to the public (sign-up,
#: login, password reset, bank lookup, webhooks, API docs), or authenticated
#: by other means (MIE developer API keys).
SELF_SERVICE_HANDLERS = {
    "APIRootView.get",
    "AcceptStaffInvitationView.post",
    "BankAccountDetailView.delete",
    "BankAccountDetailView.get",
    "BankAccountListCreateView.get",
    "BankAccountListCreateView.post",
    "BankAccountSetDefaultView.post",
    "BankListView.get",
    "CategoryViewSet.picker",
    "ChangeEmailConfirmView.post",
    "ChangeEmailRequestView.post",
    "ChangePasswordView.post",
    "CourseVersionListView.get",
    "CreatorOverviewView.get",
    "FlutterwaveWebhookView.post",
    "ForgotPasswordView.post",
    "GlobalSearchView.get",
    "GoogleLoginView.post",
    "GoogleReviewerSignupView.post",
    "GoogleSignupView.post",
    "KYCVerificationView.get",
    "KYCVerificationView.post",
    "LivenessVerificationView.post",
    "LoginView.post",
    "LogoutAllView.post",
    "LogoutView.post",
    "MFADisableView.post",
    "MFAEnrollConfirmView.post",
    "MFAEnrollView.post",
    "MFARecoveryCodesRegenerateView.post",
    "MFAVerifyView.post",
    "MeView.get",
    "MeView.patch",
    "MeView.put",
    "MieDeveloperMeView.get",
    "MieDeveloperRegistrationView.post",
    "MieDocumentationDownloadView.get",
    "MieDocumentationView.get",
    "MieSubmissionIngestView.post",
    "MieSubmissionQueueView.get",
    "MyAuditLogExportView.get",
    "NotificationListView.get",
    "NotificationPreferenceView.get",
    "NotificationPreferenceView.patch",
    "NotificationReadToggleView.post",
    "NotificationStreamView.get",
    "OnboardingView.get",
    "OnboardingView.patch",
    "PaystackWebhookView.post",
    "PlatformSettingsView.get",
    "QueueBehaviourPreferenceView.get",
    "QueueBehaviourPreferenceView.patch",
    "ResendVerificationView.post",
    "ResetPasswordView.post",
    "ReviewerActivityOverviewView.get",
    "ReviewerAvailabilityView.get",
    "ReviewerAvailabilityView.patch",
    "ReviewerOverviewView.get",
    "ReviewerSignupView.post",
    "SignupView.post",
    "SpectacularRedocView.get",
    "SpectacularSwaggerView.get",
    "SuperAdminBootstrapView.post",
    "TokenRefreshView.post",
    "TopicViewSet.list",
    "TopicViewSet.retrieve",
    "UploadAccessView.post",
    "UploadPresignView.post",
    "UserActivityLogExportView.get",
    "UserActivityLogListView.get",
    "UserSessionListView.get",
    "UserSessionRevokeView.delete",
    "VerifyBankAccountView.post",
    "VerifyEmailView.post",
    "YouverifyWebhookView.post",
}


class PermissionGateCoverageTests(SimpleTestCase):
    def test_every_handler_checks_a_permission_or_is_self_service(self):
        ungated = ungated_handlers()

        self.assertEqual(
            sorted(ungated - SELF_SERVICE_HANDLERS), [], "gate these, or list them"
        )
        self.assertEqual(
            sorted(SELF_SERVICE_HANDLERS - ungated),
            [],
            "these are gated now; remove them from the list",
        )
