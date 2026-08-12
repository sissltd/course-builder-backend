"""Swagger documentation for wallet transaction endpoints."""

from drf_spectacular.utils import OpenApiExample, OpenApiParameter

from shared.spectacular.responses import (
    STANDARD_ERROR_RESPONSES,
    inline_success_response,
)

_TRANSACTION_LIST_EXAMPLE = {
    "id": "1f6b2c94-8a70-4d31-9e52-7b0c4d8a6135",
    "reference": "TXN-4A9C13E7B052",
    "course": {
        "id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
        "title": "Introduction to Systems Design",
    },
    "amount": "150.00",
    "fee": "0.00",
    "type": "CREDIT",
    "status": "COMPLETED",
    "description": "Course 'Introduction to Systems Design' approved",
    "recipient_account_name": "",
    "recipient_account_number": "",
    "recipient_provider_name": "",
    "created_datetime": "2026-08-01T10:05:19.774Z",
}

TRANSACTION_LIST_DOCS = {
    "summary": "List wallet transactions",
    "description": (
        "Returns the authenticated creator's wallet transaction history, including "
        "every credit and debit with the associated course, amount, and settlement "
        "status. This is the ledger the wallet screen uses to show recent funding, "
        "withdrawal, and course-payout activity.\n\n"
        "Called when the wallet page loads or when the user filters the ledger by "
        "date, type, or status.\n\n"
        "**Auth:** Course Creator or Writer.\n\n"
        "**Prerequisites:** The caller must have a valid access token and a linked "
        "wallet.\n\n"
        "**Important:** Results are newest first by default and may be empty for a "
        "new creator with no wallet activity. Filter with `?type=CREDIT|DEBIT`, "
        "`?status=PENDING|COMPLETED|FAILED`, `?start_date=YYYY-MM-DD`, "
        "`?end_date=YYYY-MM-DD`, and `?ordering=created_datetime` or "
        "`?ordering=-created_datetime` to narrow the ledger."
    ),
    "tags": ["Course Creator — Wallet Transactions"],
    "parameters": [
        OpenApiParameter(
            name="type",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Filter by transaction direction: CREDIT or DEBIT.",
            required=False,
        ),
        OpenApiParameter(
            name="status",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Filter by transaction settlement state: PENDING, COMPLETED, or FAILED.",
            required=False,
        ),
        OpenApiParameter(
            name="start_date",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Only include transactions on or after this date in YYYY-MM-DD format.",
            required=False,
        ),
        OpenApiParameter(
            name="end_date",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Only include transactions on or before this date in YYYY-MM-DD format.",
            required=False,
        ),
        OpenApiParameter(
            name="search",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Search transactions by reference.",
            required=False,
        ),
        OpenApiParameter(
            name="ordering",
            type=str,
            location=OpenApiParameter.QUERY,
            description="Sort field. Use created_datetime, amount, or a leading minus sign for descending order.",
            required=False,
        ),
    ],
    "responses": {
        200: inline_success_response(
            description="Wallet transactions retrieved successfully.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value={
                        "success": True,
                        "status": 200,
                        "message": "Retrieved successfully",
                        "data": [_TRANSACTION_LIST_EXAMPLE],
                    },
                ),
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}
