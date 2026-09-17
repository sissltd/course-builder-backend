"""Swagger documentation for admin password resets and account deletion."""

from drf_spectacular.utils import OpenApiExample, OpenApiResponse

from api.users.serializers.account_admin_serializer import EraseAccountSerializer
from includes.spectacular.responses import (
    STANDARD_ERROR_RESPONSES,
    ErrorEnvelopeSerializer,
    inline_success_response,
)

_AUDIENCE = {
    "staff": {
        "tag": "Admin — Teams",
        "who": "a staff member (Writer, Verifier, Approver, QA Reviewer, AI Reviewer or Admin)",
        "reset_auth": "The `staff.reset_password` permission — Super Admin by default.",
        "erase_auth": "The `staff.delete` permission (Delete Staff) — Super Admin by default",
    },
    "teams": {
        "tag": "Admin — Users",
        "who": "a non-staff account (a Course Creator or Creator Reviewer)",
        "reset_auth": "The `teams.reset_password` permission — Admin and Super Admin by default.",
        "erase_auth": "The `teams.delete_account` permission (Delete Account) — Super Admin by default",
    },
}


def reset_docs(audience: str) -> dict:
    a = _AUDIENCE[audience]
    return {
        "summary": "Send a password reset link",
        "description": (
            f"Emails {a['who']} a password reset link, exactly as if they had "
            "used Forgot password.\n\n"
            f"**Auth:** {a['reset_auth']}\n\n"
            "**Prerequisites:** The account must be active, set up (not a "
            "pending invitation) and not yours or the Super Admin's.\n\n"
            "**Important:** The password does not change until they use the "
            "link, which also signs them out everywhere. A second request "
            "within the resend cooldown is 400. An account of the other kind "
            "(staff vs non-staff) is 404."
        ),
        "tags": [a["tag"]],
        "request": None,
        "responses": {
            200: inline_success_response(
                description="The link was sent.",
                examples=[
                    OpenApiExample(
                        name="Sent",
                        value={
                            "success": True,
                            "status": 200,
                            "message": "Password reset link sent.",
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    }


def erase_docs(audience: str) -> dict:
    a = _AUDIENCE[audience]
    return {
        "summary": "Delete an account",
        "description": (
            f"Permanently deletes {a['who']}: they can never sign in again and "
            "their personal data (name, email, phone, address, KYC details, "
            "avatar, sign-in methods, saved bank accounts) is erased.\n\n"
            f"**Auth:** {a['erase_auth']}, and a session verified with "
            "multi-factor authentication where MFA is enforced.\n\n"
            "**Prerequisites:** `confirm_email` must match the account's email. "
            "The wallet must be empty and no payout may be in progress.\n\n"
            "**Important:** Irreversible. The account row stays so courses, "
            "payouts, reviews and audit logs remain intact, now attributed to "
            "an anonymous user. Claimed but undecided review seats are released "
            "to the queue. Returns 409 while money remains on the account."
        ),
        "tags": [a["tag"]],
        "request": EraseAccountSerializer,
        "examples": [
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "reason": "User requested deletion",
                    "confirm_email": "ada@example.com",
                },
            )
        ],
        "responses": {
            200: inline_success_response(
                description="The account was deleted.",
                examples=[
                    OpenApiExample(
                        name="Deleted",
                        value={
                            "success": True,
                            "status": 200,
                            "message": "Account deleted.",
                            "data": {"id": "9f8e7d6c-5b4a-4321-8765-0fedcba98765"},
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            409: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description="The wallet still holds money, or a payout is in progress.",
                examples=[
                    OpenApiExample(
                        name="Balance",
                        value={
                            "errors": [
                                {
                                    "type": "client_error",
                                    "code": "account_has_money",
                                    "message": "This account still has a wallet balance. Pay it out or adjust it to zero before deleting the account.",
                                    "field_name": None,
                                }
                            ]
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    }
