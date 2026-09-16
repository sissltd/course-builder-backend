from django.urls import path

from api.users.views.reviewer_settings_schema_views import (
    ReviewerAccountSettingsSchemaView,
    ReviewerActivityLogExportSchemaView,
    ReviewerAuditLogExportSchemaView,
    ReviewerAvailabilitySettingsSchemaView,
    ReviewerChangeEmailConfirmSchemaView,
    ReviewerChangeEmailRequestSchemaView,
    ReviewerChangePasswordSchemaView,
    ReviewerNotificationSettingsSchemaView,
    ReviewerQueueBehaviourSettingsSchemaView,
    TAG_ACCOUNT,
    TAG_AVAILABILITY,
    TAG_NOTIFICATIONS,
    TAG_PRIVACY,
    TAG_QUEUE,
    TAG_SECURITY,
)

REVIEWER_SETTINGS_SCHEMA_SETTINGS = {
    "TITLE": "TSES Course Builder - Reviewer Settings",
    "TAGS": [
        {
            "name": TAG_ACCOUNT,
            "description": "The reviewer's own Account settings panel.",
        },
        {
            "name": TAG_AVAILABILITY,
            "description": "The reviewer's own Availability settings panel.",
        },
        {
            "name": TAG_QUEUE,
            "description": "The reviewer's own Queue Behaviour settings panel.",
        },
        {
            "name": TAG_NOTIFICATIONS,
            "description": "The reviewer's own Notification settings panel.",
        },
        {
            "name": TAG_SECURITY,
            "description": "The reviewer's own Log in & Security settings panel.",
        },
        {
            "name": TAG_PRIVACY,
            "description": "The reviewer's own Data and privacy settings panel.",
        },
    ],
}

urlpatterns = [
    path(
        "api/v1/users/me/",
        ReviewerAccountSettingsSchemaView.as_view(),
        name="reviewer-settings-account",
    ),
    path(
        "api/v1/users/me/availability/",
        ReviewerAvailabilitySettingsSchemaView.as_view(),
        name="reviewer-settings-availability",
    ),
    path(
        "api/v1/users/me/queue-preferences/",
        ReviewerQueueBehaviourSettingsSchemaView.as_view(),
        name="reviewer-settings-queue-behaviour",
    ),
    path(
        "api/v1/users/me/notification-preferences/",
        ReviewerNotificationSettingsSchemaView.as_view(),
        name="reviewer-settings-notifications",
    ),
    path(
        "api/v1/auth/change-email/",
        ReviewerChangeEmailRequestSchemaView.as_view(),
        name="reviewer-settings-change-email",
    ),
    path(
        "api/v1/auth/change-email/confirm/",
        ReviewerChangeEmailConfirmSchemaView.as_view(),
        name="reviewer-settings-change-email-confirm",
    ),
    path(
        "api/v1/auth/change-password/",
        ReviewerChangePasswordSchemaView.as_view(),
        name="reviewer-settings-change-password",
    ),
    path(
        "api/v1/users/me/activity-log/export/",
        ReviewerActivityLogExportSchemaView.as_view(),
        name="reviewer-settings-activity-log-export",
    ),
    path(
        "api/v1/users/me/audit-log/export/",
        ReviewerAuditLogExportSchemaView.as_view(),
        name="reviewer-settings-audit-log-export",
    ),
]
