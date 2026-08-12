from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.platform.serializers import (
    AdminOverviewSerializer,
    PlatformSettingsSerializer,
    PlatformSettingsUpdateSerializer,
)
from api.platform.services import admin_overview_service, platform_settings_service
from api.users.permissions import IsAdminOrSuperAdminRole, IsMFAVerifiedForSession
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_SETTINGS_EXAMPLE = {
    "id": "2e9c4a71-58b3-4d06-9f27-6a1e8c0b5d34",
    "minimum_withdrawal_threshold": "50.00",
    "course_module_count_min": 4,
    "course_module_count_max": 12,
    "course_lessons_per_module_min": 3,
    "course_lessons_per_module_max": 8,
    "course_learning_objectives_min": 2,
    "course_learning_objectives_max": 5,
    "course_description_word_min": 100,
    "course_description_word_max": 500,
    "lesson_script_word_min": 500,
    "lesson_script_word_max": 1500,
    "lesson_quiz_questions_min": 3,
    "lesson_quiz_questions_max": 5,
    "course_duration_min_minutes": 120,
    "course_duration_max_minutes": 480,
    "course_final_assessment_min_questions": 15,
    "topic_reservation_expiry_days": 14,
    "updated_datetime": "2026-08-06T09:22:41.508Z",
}


class PlatformSettingsView(APIView):
    """GET/PATCH the platform's singleton settings row.

    GET is open to any authenticated user - the frontend needs to display
    live thresholds (e.g. "4-12 modules required") without hardcoding them.
    PATCH is Admin/Super Admin only (excludes Approver - these thresholds
    affect every course on the platform, a higher-stakes action than
    ordinary admin-tier work) and additionally requires an MFA-verified
    session - see IsMFAVerifiedForSession.
    """

    serializer_class = PlatformSettingsUpdateSerializer  # for schema generation only

    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsAdminOrSuperAdminRole(), IsMFAVerifiedForSession()]
        return [IsAuthenticated()]

    @extend_schema(
        summary="Retrieve platform settings",
        description=(
            "Returns the live platform-wide thresholds that govern course "
            "validation and withdrawals — module and lesson counts, word "
            "limits, the minimum withdrawal amount, and how long a topic "
            "reservation is held. These used to be environment variables and "
            "are now database-backed, so the frontend must read them here "
            "rather than hardcoding numbers that an Admin can change.\n\n"
            "Called when rendering any screen that states a rule to the user: "
            "the course builder's validation hints, the withdrawal form's "
            "minimum, and the admin Settings screen.\n\n"
            "**Auth:** Any authenticated user. Everyone needs to read the "
            "rules; only Admins and Super Admins can change them.\n\n"
            "**Prerequisites:** None beyond being signed in.\n\n"
            "**Important:** Exactly one settings row ever exists — it is "
            "created with default values on first access, so this never 404s. "
            "Values can change at any time without a deploy; do not cache them "
            "across sessions."
        ),
        tags=["Creator — Platform Settings"],
        responses={
            200: OpenApiResponse(
                response=PlatformSettingsSerializer,
                description="The current platform settings.",
                examples=[OpenApiExample(name="Success", value=_SETTINGS_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        settings_row = platform_settings_service.get_settings()
        return Response(PlatformSettingsSerializer(settings_row).data)

    @extend_schema(
        summary="Update platform settings",
        description=(
            "Changes one or more platform-wide thresholds. Every field is "
            "optional, so the Settings screen can save a single knob without "
            "resubmitting the whole form.\n\n"
            "Called from the admin Settings screen when a threshold is "
            "saved.\n\n"
            "**Auth:** Admin or Super Admin. Approvers are excluded — these "
            "values affect every course on the platform, which is a higher-"
            "stakes change than the course approvals Approvers handle.\n\n"
            "**Prerequisites:** At least one settings field must be present in "
            "the body.\n\n"
            "**Important:** Changes take effect immediately and apply to the "
            "next validation, not retroactively — a course already submitted "
            "keeps the price snapshot it was submitted with, and courses "
            "already approved are never re-validated. Raising a minimum can "
            "therefore make an in-progress draft invalid at submission time. "
            "An empty body returns 400 rather than silently doing nothing."
        ),
        tags=["Admin — Platform Settings"],
        request=PlatformSettingsUpdateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "minimum_withdrawal_threshold": "75.00",
                    "course_module_count_min": 5,
                },
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=PlatformSettingsSerializer,
                description="The full settings row after the update.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            **_SETTINGS_EXAMPLE,
                            "minimum_withdrawal_threshold": "75.00",
                            "course_module_count_min": 5,
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def patch(self, request):
        serializer = PlatformSettingsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        settings_row = platform_settings_service.update_settings(
            **serializer.validated_data
        )
        return Response(PlatformSettingsSerializer(settings_row).data)


class AdminOverviewView(APIView):
    """Aggregate counts and money totals for the admin home screen."""

    permission_classes = [IsAdminOrSuperAdminRole]
    serializer_class = AdminOverviewSerializer  # for schema generation only

    @extend_schema(
        summary="Retrieve the admin dashboard overview",
        description=(
            "Returns the counts an Admin's home screen leads with: users by "
            "account status, courses by lifecycle status, KYC submissions and "
            "withdrawal requests by status, plus platform-wide wallet totals. "
            "It answers 'what needs my attention today?' in one call.\n\n"
            "Called when the admin dashboard loads.\n\n"
            "**Auth:** Admin or Super Admin.\n\n"
            "**Prerequisites:** None beyond holding the Admin or Super Admin "
            "role.\n\n"
            "**Important:** Counts only — each figure has a dedicated endpoint "
            "behind it (`/review-queue/`, `/users/kyc-review/`, "
            "`/admin/withdrawals/`) and this deliberately does not duplicate "
            "those payloads. Every status key is always present, including "
            "zeroes, so tiles do not appear and disappear with the data. "
            "Figures are computed live on each request and are not cached. "
            "`awaiting_payout` is money already deducted from creator balances "
            "but not yet settled — the platform has no settlement step yet, so "
            "expect it to accumulate."
        ),
        tags=["Admin — Overview"],
        responses={
            200: OpenApiResponse(
                response=AdminOverviewSerializer,
                description="Platform-wide counts and wallet totals.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "users": {
                                "PENDING_VERIFICATION": 12,
                                "ACTIVE": 486,
                                "SUSPENDED": 3,
                                "DEACTIVATED": 7,
                            },
                            "courses": {
                                "DRAFT": 94,
                                "SUBMITTED": 18,
                                "IN_REVIEW": 6,
                                "APPROVED": 152,
                                "REJECTED": 0,
                                "PUBLISHED": 131,
                            },
                            "kyc": {"PENDING": 9, "APPROVED": 274, "REJECTED": 11},
                            "withdrawals": {
                                "PENDING_CONFIRMATION": 4,
                                "CONFIRMED": 63,
                                "EXPIRED": 0,
                            },
                            "wallet_totals": {
                                "balance_held": "18450.00",
                                "total_credited": "42300.00",
                                "awaiting_payout": "3200.00",
                            },
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        overview = admin_overview_service.get_overview(actor=request.user)
        return Response(AdminOverviewSerializer(overview).data)
