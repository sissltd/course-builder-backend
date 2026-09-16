from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from api.authentication.serializers import (
    ChangeEmailConfirmSerializer,
    ChangeEmailRequestSerializer,
    ChangePasswordSerializer,
)
from api.authentication.views import (
    ChangeEmailConfirmView,
    ChangeEmailRequestView,
    ChangePasswordView,
)
from api.notification.serializers import (
    NotificationPreferenceSerializer,
    NotificationPreferenceUpdateSerializer,
)
from api.notification.views import NotificationPreferenceView
from api.users.serializers import (
    MeSerializer,
    MeUpdateSerializer,
    QueueBehaviourPreferenceSerializer,
    QueueBehaviourPreferenceUpdateSerializer,
    ReviewerAvailabilitySerializer,
    ReviewerAvailabilityUpdateSerializer,
)
from api.users.views.activity_log_views import UserActivityLogExportView
from api.users.views.user_views import (
    MeView,
    QueueBehaviourPreferenceView,
    ReviewerAvailabilityView,
)
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES
from shared.audit.views import MyAuditLogExportView

TAG_ACCOUNT = "Reviewer Settings — Account"
TAG_AVAILABILITY = "Reviewer Settings — Availability"
TAG_QUEUE = "Reviewer Settings — Queue Behaviour"
TAG_NOTIFICATIONS = "Reviewer Settings — Notification Settings"
TAG_SECURITY = "Reviewer Settings — Log in & Security"
TAG_PRIVACY = "Reviewer Settings — Data & Privacy"

_DETAIL_SCHEMA = {"type": "object", "properties": {"detail": {"type": "string"}}}


class ReviewerAccountSettingsSchemaView(MeView):
    @extend_schema(
        summary="View reviewer account settings",
        description=(
            "Returns the signed-in reviewer's account details for the "
            "Account settings panel: name, email, contact details, timezone, "
            "avatar, role, status, verification state, and account metadata.\n\n"
            "Called when the reviewer opens Settings → Account, and after "
            "saving account changes so the form can refresh from server state.\n\n"
            "**Auth:** Authenticated reviewer account.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Email is read-only here. Use the Log in & "
            "Security email-change flow to change it."
        ),
        tags=[TAG_ACCOUNT],
        responses={
            200: OpenApiResponse(
                response=MeSerializer,
                description="The signed-in reviewer's account settings.",
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        summary="Update reviewer account settings",
        description=(
            "Partially updates the signed-in reviewer's Account settings. "
            "Only submitted fields are changed, so the frontend can save "
            "one field group without resending the whole profile.\n\n"
            "Called from Settings → Account after the reviewer saves their "
            "profile details.\n\n"
            "**Auth:** Authenticated reviewer account.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Email cannot be changed here. If `category` is "
            "sent it must be an active category id; omit fields that should "
            "stay unchanged."
        ),
        tags=[TAG_ACCOUNT],
        request=MeUpdateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "first_name": "Ada",
                    "last_name": "Nwosu",
                    "timezone": "Africa/Lagos",
                    "phone_number": "+2348012345678",
                    "country": "NG",
                    "state": "Lagos",
                    "address": "14 Admiralty Way, Lekki Phase 1",
                },
            )
        ],
        responses={
            200: OpenApiResponse(
                response=MeSerializer,
                description="The updated reviewer account settings.",
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)


class ReviewerAvailabilitySettingsSchemaView(ReviewerAvailabilityView):
    @extend_schema(
        summary="View reviewer availability settings",
        description=(
            "Returns the reviewer's Availability settings, creating the "
            "default available row the first time this panel is opened.\n\n"
            "Called when the reviewer opens Settings → Availability.\n\n"
            "**Auth:** Authenticated reviewer account.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** `is_effectively_available` includes the auto-"
            "return rule. If the return date has passed and auto-return is "
            "enabled, the reviewer is treated as available."
        ),
        tags=[TAG_AVAILABILITY],
        responses={
            200: OpenApiResponse(response=ReviewerAvailabilitySerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        return super().get(request)

    @extend_schema(
        summary="Update reviewer availability settings",
        description=(
            "Updates one or more Availability settings: available status, "
            "unavailability reason, expected return date, and auto-return.\n\n"
            "Called from Settings → Availability when the reviewer saves the "
            "panel.\n\n"
            "**Auth:** Authenticated reviewer account.\n\n"
            "**Prerequisites:** At least one availability field must be sent.\n\n"
            "**Important:** Setting `is_available` to true clears the "
            "unavailability reason and return date because they only apply "
            "while the reviewer is unavailable."
        ),
        tags=[TAG_AVAILABILITY],
        request=ReviewerAvailabilityUpdateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "is_available": False,
                    "unavailability_reason": "VACATION",
                    "return_date": "2026-10-05",
                    "auto_return_enabled": True,
                },
            )
        ],
        responses={
            200: OpenApiResponse(response=ReviewerAvailabilitySerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def patch(self, request):
        return super().patch(request)


class ReviewerQueueBehaviourSettingsSchemaView(QueueBehaviourPreferenceView):
    @extend_schema(
        summary="View reviewer queue behaviour settings",
        description=(
            "Returns the reviewer's Queue Behaviour settings, creating "
            "default preferences the first time this panel is opened.\n\n"
            "Called when the reviewer opens Settings → Queue Behaviour.\n\n"
            "**Auth:** Authenticated reviewer account.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** `effective_track_filter` is derived from the "
            "three track toggles and tells the frontend what queue content "
            "will actually be shown."
        ),
        tags=[TAG_QUEUE],
        responses={
            200: OpenApiResponse(response=QueueBehaviourPreferenceSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        return super().get(request)

    @extend_schema(
        summary="Update reviewer queue behaviour settings",
        description=(
            "Updates the reviewer's default queue sort order and track "
            "toggles exactly as shown in the Queue Behaviour settings panel.\n\n"
            "Called from Settings → Queue Behaviour when the reviewer saves "
            "their queue preferences.\n\n"
            "**Auth:** Authenticated reviewer account.\n\n"
            "**Prerequisites:** At least one queue behaviour field must be sent.\n\n"
            "**Important:** If all three track toggles are off, "
            "`effective_track_filter` becomes `NONE`, producing an "
            "intentionally empty queue."
        ),
        tags=[TAG_QUEUE],
        request=QueueBehaviourPreferenceUpdateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "default_sort_order": "NEWEST_FIRST",
                    "auto_advance_enabled": True,
                    "show_ai_track": False,
                    "show_creator_track": True,
                    "show_both_track": False,
                },
            )
        ],
        responses={
            200: OpenApiResponse(response=QueueBehaviourPreferenceSerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def patch(self, request):
        return super().patch(request)


class ReviewerNotificationSettingsSchemaView(NotificationPreferenceView):
    @extend_schema(
        summary="View reviewer notification settings",
        description=(
            "Returns the reviewer's Notification settings, including queue "
            "alerts, SLA alerts, in-app notification master toggle, and SLA "
            "threshold overrides.\n\n"
            "Called when the reviewer opens Settings → Notification settings.\n\n"
            "**Auth:** Authenticated reviewer account.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Some toggles are reserved for alerting systems "
            "that may be inert until their underlying trigger exists. The "
            "preference values are still stored and round-tripped."
        ),
        tags=[TAG_NOTIFICATIONS],
        responses={
            200: OpenApiResponse(response=NotificationPreferenceSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        return super().get(request)

    @extend_schema(
        summary="Update reviewer notification settings",
        description=(
            "Updates one or more Notification settings. Every field is "
            "optional so the frontend can save a single toggle or threshold "
            "without resending the whole panel.\n\n"
            "Called from Settings → Notification settings when the reviewer "
            "saves notification preferences.\n\n"
            "**Auth:** Authenticated reviewer account.\n\n"
            "**Prerequisites:** At least one notification preference must be sent.\n\n"
            "**Important:** Send `null` for an SLA threshold override to "
            "clear the personal override and fall back to the platform default."
        ),
        tags=[TAG_NOTIFICATIONS],
        request=NotificationPreferenceUpdateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "new_course_assigned": True,
                    "sla_amber_warning": True,
                    "sla_red_critical_alert": True,
                    "in_app_enabled": True,
                    "sla_amber_threshold_hours_override": 48,
                    "sla_red_threshold_hours_override": 72,
                },
            )
        ],
        responses={
            200: OpenApiResponse(response=NotificationPreferenceSerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def patch(self, request):
        return super().patch(request)


class ReviewerChangeEmailRequestSchemaView(ChangeEmailRequestView):
    @extend_schema(
        summary="Request reviewer email change",
        description=(
            "Starts the Log in & Security email-change flow by verifying the "
            "current password and sending a confirmation link to the new email.\n\n"
            "Called from Settings → Log in & Security when the reviewer "
            "chooses Change email.\n\n"
            "**Auth:** Authenticated reviewer account.\n\n"
            "**Prerequisites:** `password` must match the current account "
            "password and `new_email` must not already be in use.\n\n"
            "**Important:** This call does not change `User.email`; the "
            "email changes only after the confirmation link is consumed."
        ),
        tags=[TAG_SECURITY],
        request=ChangeEmailRequestSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "new_email": "ada.reviewer@example.com",
                    "password": "StrongPass123!",
                },
            )
        ],
        responses={
            200: OpenApiResponse(response=_DETAIL_SCHEMA),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        return super().post(request)


class ReviewerChangeEmailConfirmSchemaView(ChangeEmailConfirmView):
    @extend_schema(
        summary="Confirm reviewer email change",
        description=(
            "Consumes the email-change confirmation token and applies the "
            "reviewer's new account email address.\n\n"
            "Called when the reviewer opens the confirmation link sent to "
            "their new email inbox.\n\n"
            "**Auth:** Public - the token is the credential.\n\n"
            "**Prerequisites:** A valid email-change request must have been "
            "created and the token must be unused and unexpired.\n\n"
            "**Important:** After this succeeds, the old email can no "
            "longer be used to sign in."
        ),
        tags=[TAG_SECURITY],
        request=ChangeEmailConfirmSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={"token": "8Kj2mNqR7vXyB4dW1sHfL6pT0aZcE3gU9nY5bV8rQmI"},
            )
        ],
        responses={
            200: OpenApiResponse(response=_DETAIL_SCHEMA),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        return super().post(request)


class ReviewerChangePasswordSchemaView(ChangePasswordView):
    @extend_schema(
        summary="Change reviewer password",
        description=(
            "Changes the reviewer's password after confirming the current "
            "password.\n\n"
            "Called from Settings → Log in & Security when the reviewer "
            "submits the Change password form.\n\n"
            "**Auth:** Authenticated reviewer account.\n\n"
            "**Prerequisites:** `current_password` must match the account's "
            "current password.\n\n"
            "**Important:** Existing sessions on other devices are not "
            "automatically revoked by this call."
        ),
        tags=[TAG_SECURITY],
        request=ChangePasswordSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "current_password": "StrongPass123!",
                    "new_password": "BrandNewPass456!",
                },
            )
        ],
        responses={
            200: OpenApiResponse(response=_DETAIL_SCHEMA),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        return super().post(request)


class ReviewerActivityLogExportSchemaView(UserActivityLogExportView):
    @extend_schema(
        summary="Export reviewer activity log",
        description=(
            "Downloads the reviewer's own activity history as a CSV file.\n\n"
            "Called from Settings → Data and privacy when the reviewer "
            "clicks Download activity log.\n\n"
            "**Auth:** Authenticated reviewer account.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** The export is self-scoped to the caller. The "
            "response is `text/csv` as an attachment, not JSON."
        ),
        tags=[TAG_PRIVACY],
        request=None,
        responses={(200, "text/csv"): OpenApiResponse(description="CSV file.")},
    )
    def get(self, request):
        return super().get(request)


class ReviewerAuditLogExportSchemaView(MyAuditLogExportView):
    @extend_schema(
        summary="Export reviewer audit trail",
        description=(
            "Downloads the reviewer's own audit-trail entries as a CSV file.\n\n"
            "Called from Settings → Data and privacy when the reviewer "
            "clicks Download audit trail entries.\n\n"
            "**Auth:** Authenticated reviewer account.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Scoped to the caller's own email server-side; "
            "no query parameter can widen it to another account. The "
            "response is `text/csv` as an attachment, not JSON."
        ),
        tags=[TAG_PRIVACY],
        request=None,
        responses={(200, "text/csv"): OpenApiResponse(description="CSV file.")},
    )
    def get(self, request):
        return super().get(request)
