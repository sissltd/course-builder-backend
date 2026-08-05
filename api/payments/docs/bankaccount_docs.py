"""Swagger documentation for wallet bank account endpoints."""

from drf_spectacular.utils import OpenApiExample, OpenApiParameter

from api.payments.serializers.bankaccount_serializers import (
    BankAccountCreateSerializer,
    BankAccountVerifySerializer,
)
from shared.spectacular.responses import (
	STANDARD_ERROR_RESPONSES,
	inline_success_response,
)

_BANK_ACCOUNT_LIST_EXAMPLE = {
    "id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
    "bank_name": "Guaranty Trust Bank",
    "account_name": "Jane Doe",
    "account_number": "0123456789",
    "bank_code": "058",
    "is_default": True,
    # "withdrawal_time_limit_days": 3,
}

_BANK_ACCOUNT_CREATE_EXAMPLE = {
    "bank_name": "Guaranty Trust Bank",
    "account_name": "Jane Doe",
    "account_number": "0123456789",
    "bank_code": "058",
    "is_default": True,
}


BANK_ACCOUNT_LIST_DOCS = {
    "summary": "List bank accounts",
    "description": (
        "Returns the authenticated user's saved bank accounts for the wallet "
        "screen, including the masked account details and the withdrawal time "
        "limit derived from the linked wallet.\n\n"
        "Called when the user opens the bank accounts page or refreshes the "
        "list after adding, deleting, or changing the default account.\n\n"
        "**Auth:** Authenticated user.\n\n"
        "**Prerequisites:** The caller must have a valid access token.\n\n"
        "**Important:** The response only includes bank accounts available to "
        "the authenticated user, and the account number is returned in its "
        "decrypted form for display."
    ),
    "tags": ["Course Creator — Bank Accounts"],
    "responses": {
		200: inline_success_response(
			description="Bank accounts retrieved successfully.",
			examples=[
				OpenApiExample(
					name="Success",
					value={
						"success": True,
						"status": 200,
						"message": "Retrieved successfully",
						"data": [_BANK_ACCOUNT_LIST_EXAMPLE],
					},
				),
			],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}


BANK_ACCOUNT_CREATE_DOCS = {
    "summary": "Create a bank account",
    "description": (
        "Creates and stores a bank account for the authenticated user after "
        "validating the submitted details and resolving the bank name from "
        "Paystack. The account becomes available immediately in the wallet "
        "UI if the request succeeds.\n\n"
        "Called from the add-bank-account form.\n\n"
        "**Auth:** Authenticated user.\n\n"
        "**Prerequisites:** The caller must have a valid access token and the "
        "account name must match the user's profile closely enough for the "
        "server-side name check to pass.\n\n"
        "**Important:** This endpoint can return a 400 even when the serializer "
        "is valid, for example if the account name does not match the user "
        "profile or the account is suspended."
    ),
    "tags": ["Course Creator — Bank Accounts"],
    "request": BankAccountCreateSerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request",
            request_only=True,
            value={
                "account_name": "Jane Doe",
                "account_number": "0123456789",
                "bank_code": "058",
                "account_type": "Local Account",
                "is_default": True,
            },
        ),
    ],
    "responses": {
        201: inline_success_response(
            description="Bank account created successfully.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value={
                        "success": True,
                        "status": 201,
                        "message": "Bank account added successfully",
                        "data": {"bank_account": _BANK_ACCOUNT_LIST_EXAMPLE},
                    },
                ),
            ],
        ),
        **STANDARD_ERROR_RESPONSES["validation"],
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BANK_ACCOUNT_DETAIL_DOCS = {
    "summary": "Retrieve a bank account",
    "description": (
        "Returns a single bank account belonging to the authenticated user. "
        "This is used when the wallet UI needs to display the details of one "
        "saved account or confirm that it still exists before editing or "
        "deleting it.\n\n"
        "**Auth:** Authenticated user.\n\n"
        "**Prerequisites:** The caller must have a valid access token and the "
        "bank account must belong to the authenticated user and not be soft-deleted."
    ),
    "tags": ["Course Creator — Bank Accounts"],
    "parameters": [
        OpenApiParameter(
            name="pk",
            type=str,
            location=OpenApiParameter.PATH,
            description="Unique identifier of the bank account to retrieve.",
        ),
    ],
    "responses": {
        200: inline_success_response(
            description="Bank account retrieved successfully.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value={
                        "success": True,
                        "status": 200,
                        "message": "Retrieved successfully",
                        "data": {
                            **_BANK_ACCOUNT_LIST_EXAMPLE,
                            "id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
                        },
                    },
                ),
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BANK_ACCOUNT_DELETE_DOCS = {
    "summary": "Delete a bank account",
    "description": (
        "Soft-deletes a bank account belonging to the authenticated user. "
        "This is used when the wallet UI removes a saved account after the "
        "user confirms that they no longer want to keep it.\n\n"
        "**Auth:** Authenticated user.\n\n"
        "**Prerequisites:** The caller must have a valid access token and the "
        "bank account must belong to the authenticated user and not already "
        "be soft-deleted."
    ),
    "tags": ["Course Creator — Bank Accounts"],
    "parameters": [
        OpenApiParameter(
            name="pk",
            type=str,
            location=OpenApiParameter.PATH,
            description="Unique identifier of the bank account to delete.",
        ),
    ],
    "responses": {
        204: inline_success_response(
            description="Bank account deleted successfully.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value={
                        "success": True,
                        "status": 204,
                        "message": "Bank account deleted successfully",
                    },
                ),
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BANK_ACCOUNT_SET_DEFAULT_DOCS = {
    "summary": "Set a default bank account",
    "description": (
        "Marks a saved bank account as the authenticated user's default payout "
        "destination. This is used by the wallet UI after the user picks a "
        "different preferred account for withdrawals.\n\n"
        "**Auth:** Authenticated user.\n\n"
        "**Prerequisites:** The caller must have a valid access token and the "
        "selected bank account must belong to the authenticated user and not "
        "already be the default account."
    ),
    "tags": ["Course Creator — Bank Accounts"],
    "parameters": [
        OpenApiParameter(
            name="pk",
            type=str,
            location=OpenApiParameter.PATH,
            description="Unique identifier of the bank account to set as default.",
        ),
    ],
    "responses": {
        200: inline_success_response(
            description="Bank account set as default successfully.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value={
                        "success": True,
                        "status": 200,
                        "message": "Bank account set as default successfully",
                    },
                ),
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BANK_ACCOUNT_SUSPEND_DOCS = {
    "summary": "Suspend a bank account",
    "description": (
        "Marks a saved bank account as suspended for the authenticated user. "
        "This is used when a bank account should no longer be used for "
        "withdrawals while keeping the record for audit and support purposes.\n\n"
        "**Auth:** Admin user.\n\n"
        "**Prerequisites:** The caller must have a valid access token and the "
        "bank account must belong to the authenticated user and not already "
        "be suspended."
    ),
    "tags": ["Admin — Bank Accounts"],
    "parameters": [
        OpenApiParameter(
            name="pk",
            type=str,
            location=OpenApiParameter.PATH,
            description="Unique identifier of the bank account to suspend.",
        ),
    ],
    "responses": {
        200: inline_success_response(
            description="Bank account suspended successfully.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value={
                        "success": True,
                        "status": 200,
                        "message": "Bank account suspended successfully",
                    },
                ),
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BANK_ACCOUNT_VERIFY_DOCS = {
    "summary": "Verify a bank account",
    "description": (
        "Validates a bank account number against a selected bank code using "
        "the Paystack resolver. This endpoint is used by the wallet UI before "
        "saving a new bank account so the user can confirm the account name "
        "and details.\n\n"
        "**Auth:** Public endpoint. No authentication is required.\n\n"
        "**Prerequisites:** The caller must provide a valid bank code and "
        "account number."
    ),
    "tags": ["Course Creator — Bank Accounts"],
    "request": BankAccountVerifySerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request",
            request_only=True,
            value={
                "bank_code": "058",
                "account_number": "0123456789",
            },
        ),
    ],
    "responses": {
        200: inline_success_response(
            description="Bank account verified successfully.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value={
                        "success": True,
                        "status": 200,
                        "message": "Bank account verified successfully",
                        "data": {
                            "account_name": "Jane Doe",
                            "account_number": "0123456789",
                            "bank_code": "058",
                        },
                    },
                ),
            ],
        ),
        **STANDARD_ERROR_RESPONSES["validation"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BANK_LIST_DOCS = {
    "summary": "List supported banks",
    "description": (
        "Returns the public list of supported banks and bank codes from "
        "Paystack. This endpoint is used by the wallet UI to populate the "
        "bank selection dropdown during account creation.\n\n"
        "**Auth:** Public endpoint. No authentication is required."
    ),
    "tags": ["Public — Bank Accounts"],
    "responses": {
        200: inline_success_response(
            description="Banks retrieved successfully.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value={
                        "success": True,
                        "status": 200,
                        "message": "Processed successfully",
                        "data": [
                            {
                                "name": "Guaranty Trust Bank",
                                "code": "058",
                            }
                        ],
                    },
                ),
            ],
        ),
        **STANDARD_ERROR_RESPONSES["server"],
    },
}
