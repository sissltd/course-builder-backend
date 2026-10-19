"""Swagger documentation for the Support page (contact, tickets, appeals)
and the admin Tickets page. See docs/standards/swagger_standard.md.

Support endpoints return the serializer data directly (no success
envelope); list endpoints are wrapped by the project paginator."""

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse

from api.support.enums import SupportRequestKind, SupportRequestStatus
from api.support.serializers import (
    SupportContactCreateSerializer,
    SupportRequestSerializer,
    SupportResolveSerializer,
    SupportTicketBulkResultSerializer,
    SupportTicketBulkStatusSerializer,
    SupportTicketCreateSerializer,
    SupportTicketDetailSerializer,
    SupportTicketMessageSerializer,
    SupportTicketReplySerializer,
    SupportTicketStatusCountsSerializer,
    SupportTicketStatusSerializer,
    SupportTitledRequestCreateSerializer,
)
from includes.spectacular.responses import (
    STANDARD_ERROR_RESPONSES,
    ErrorEnvelopeSerializer,
)

PUBLIC_TAG = "Public — Support"
TICKETS_TAG = "Creator — Support Tickets"
APPEALS_TAG = "Creator — Support Appeals"
ADMIN_REQUESTS_TAG = "Admin — Support Requests"
ADMIN_TICKETS_TAG = "Admin — Support Tickets"

_SIGNED_IN_AUTH = (
    "**Auth:** Any signed-in user (Bearer token) — in practice Course "
    "Creators and Writers from Help > Support.\n\n"
)
_ADMIN_AUTH = (
    "**Auth:** The `support.manage_requests` permission — Admin and Super "
    "Admin by default.\n\n"
)

# --- Example payloads -------------------------------------------------------

_CONTACT_EXAMPLE = {
    "id": "5e1f7a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b",
    "kind": "CONTACT",
    "first_name": "Ada",
    "last_name": "Obi",
    "email": "ada.obi@example.com",
    "country": "NG",
    "title": "",
    "web_link": "",
    "message": "How do I change the bank account my payouts go to?",
    "status": "OPEN",
    "priority": "",
    "due_at": None,
    "resolution_notes": "",
    "resolved_at": None,
    "submitted_by_email": None,
    "submitted_by_name": None,
    "assigned_to_name": None,
    "created_datetime": "2026-10-19T09:15:00Z",
    "updated_datetime": "2026-10-19T09:15:00Z",
}

_APPEAL_EXAMPLE = {
    **_CONTACT_EXAMPLE,
    "id": "0b7c3f5e-6a1d-4e2b-9c8f-2d4a6b8c0e13",
    "kind": "APPEAL",
    "email": "ada.obi@example.com",
    "country": "",
    "title": "My account was suspended in error",
    "web_link": "https://adaobi.dev/portfolio",
    "message": "The flagged lesson was my original work; please review it.",
    "due_at": "2026-10-28T09:15:00Z",
    "submitted_by_email": "ada.obi@example.com",
    "submitted_by_name": "Ada Obi",
}

_TICKET_EXAMPLE = {
    **_APPEAL_EXAMPLE,
    "id": "fea458f1-ad9e-494d-8cea-2d4a6b8c0e13",
    "kind": "TICKET",
    "title": "KYC failure during log in",
    "web_link": "",
    "message": (
        "I have been experiencing repeated KYC failure. Please can this be "
        "rectified so I can complete my registration."
    ),
    "status": "IN_PROGRESS",
    "priority": "HIGH",
    "due_at": None,
    "assigned_to_name": "Osaite Emmanuel",
    "updated_datetime": "2026-10-19T09:32:00Z",
}

_ADMIN_ID = "3c4d5e6f-7a8b-4c9d-8e1f-2a3b4c5d6e7f"
_CREATOR_ID = "7a0d6c51-8e4b-4f2a-9b3e-1c2d3e4f5a6b"

_STATUS_CHANGE_MESSAGE = {
    "id": "1d2c3b4a-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    "kind": "STATUS_CHANGE",
    "body": "",
    "from_status": "OPEN",
    "to_status": "IN_PROGRESS",
    "author_id": _ADMIN_ID,
    "author_name": "Osaite Emmanuel",
    "from_support": True,
    "is_mine": True,
    "created_datetime": "2026-10-19T09:30:00Z",
}

_SUPPORT_REPLY_MESSAGE = {
    "id": "2e3d4c5b-6f7a-4b8c-9d0e-1f2a3b4c5d6e",
    "kind": "REPLY",
    "body": (
        "Your KYC details will be resolved shortly. In the meantime, try "
        "submitting again in 24 hours."
    ),
    "from_status": "",
    "to_status": "",
    "author_id": _ADMIN_ID,
    "author_name": "Osaite Emmanuel",
    "from_support": True,
    "is_mine": True,
    "created_datetime": "2026-10-19T09:32:00Z",
}

_CREATOR_REPLY_MESSAGE = {
    "id": "3f4e5d6c-7a8b-4c9d-8e0f-1a2b3c4d5e6f",
    "kind": "REPLY",
    "body": "Ok. Thanks",
    "from_status": "",
    "to_status": "",
    "author_id": _CREATOR_ID,
    "author_name": "Ada Obi",
    "from_support": False,
    "is_mine": True,
    "created_datetime": "2026-10-19T09:40:00Z",
}

_TICKET_DETAIL_EXAMPLE = {
    **_TICKET_EXAMPLE,
    "submitted_by": {
        "id": _CREATOR_ID,
        "name": "Ada Obi",
        "email": "ada.obi@example.com",
        "role": "Course Creator",
    },
    "messages": [
        _STATUS_CHANGE_MESSAGE,
        _SUPPORT_REPLY_MESSAGE,
        {**_CREATOR_REPLY_MESSAGE, "is_mine": False},
    ],
}


def _page(results: list) -> dict:
    """The project paginator's wrapper around a list page."""

    return {
        "status": True,
        "message": "Successfully retrieved data",
        "data": {
            "paginator": {
                "count": len(results),
                "page": 1,
                "page_size": 10,
                "total_pages": 1,
                "next": None,
                "next_page_number": None,
                "previous": None,
                "previous_page_number": None,
            },
            "results": results,
        },
    }


def _ok(serializer, description: str, name: str, value) -> OpenApiResponse:
    return OpenApiResponse(
        response=serializer,
        description=description,
        examples=[OpenApiExample(name=name, value=value)],
    )


def _bad_request(description: str, examples: dict[str, tuple]) -> dict:
    """A 400 with endpoint-specific examples; each value is
    (code, message, field_name)."""

    return {
        400: OpenApiResponse(
            response=ErrorEnvelopeSerializer,
            description=description,
            examples=[
                OpenApiExample(
                    name=name,
                    value={
                        "errors": [
                            {
                                "type": "validation_error",
                                "code": code,
                                "message": message,
                                "field_name": field_name,
                            }
                        ]
                    },
                )
                for name, (code, message, field_name) in examples.items()
            ],
        )
    }


def _not_found(
    description: str, name: str = "Not found", message: str = "Not found."
) -> dict:
    return {
        404: OpenApiResponse(
            response=ErrorEnvelopeSerializer,
            description=description,
            examples=[
                OpenApiExample(
                    name=name,
                    value={
                        "errors": [
                            {
                                "type": "client_error",
                                "code": "not_found",
                                "message": message,
                                "field_name": None,
                            }
                        ]
                    },
                )
            ],
        )
    }


_INVALID_PAGE = _not_found(
    "`page` is past the last page.", name="Invalid page", message="Invalid page."
)


_TICKET_NOT_FOUND = _not_found(
    "No ticket with this ID — or it belongs to someone else."
)
_ADMIN_TICKET_NOT_FOUND = _not_found("No ticket with this ID.")

_REPLY_ERRORS = _bad_request(
    "The reply body is missing, or the ticket is closed.",
    {
        "Missing body": ("required", "This field is required.", "body"),
        "Ticket closed": (
            "invalid",
            "This ticket is closed and no longer accepts replies.",
            "body",
        ),
    },
)

# --- Contact ----------------------------------------------------------------

SUPPORT_CONTACT_DOCS = {
    "summary": "Send a contact message",
    "description": (
        "Records a 'Contact us' message and notifies Support staff in-app so "
        "it appears in the staff support queue.\n\n"
        "Called from the Support page's 'Contact us' action and the public "
        "contact page. Staff reply to the typed `email`.\n\n"
        "**Auth:** Public — no auth required. A valid Bearer token is "
        "optional: when sent, the message is linked to the caller's account "
        "(`submitted_by_email` / `submitted_by_name` are filled). A missing, "
        "expired or invalid token is ignored — never 401.\n\n"
        "**Prerequisites:** None.\n\n"
        "**Important:** Rate-limited to 5 messages per hour — per account "
        "when a valid token is sent, otherwise per client IP; the sixth "
        "returns 429 with a `Retry-After` header. `country` is a "
        "two-letter ISO 3166-1 code and is stored upper-cased."
    ),
    "tags": [PUBLIC_TAG],
    "auth": [{}, {"bearerAuth": []}],
    "request": SupportContactCreateSerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request",
            request_only=True,
            value={
                "first_name": "Ada",
                "last_name": "Obi",
                "email": "ada.obi@example.com",
                "country": "NG",
                "message": "How do I change the bank account my payouts go to?",
            },
        )
    ],
    "responses": {
        201: OpenApiResponse(
            response=SupportRequestSerializer,
            description="Message recorded.",
            examples=[
                OpenApiExample(name="Anonymous", value=_CONTACT_EXAMPLE),
                OpenApiExample(
                    name="Signed in",
                    value={
                        **_CONTACT_EXAMPLE,
                        "submitted_by_email": "ada.obi@example.com",
                        "submitted_by_name": "Ada Obi",
                    },
                ),
            ],
        ),
        **_bad_request(
            "A field is missing or malformed.",
            {
                "Missing field": ("required", "This field is required.", "message"),
                "Bad country": (
                    "invalid",
                    "Use a two-letter ISO country code.",
                    "country",
                ),
            },
        ),
        **STANDARD_ERROR_RESPONSES["rate_limited"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

# --- Creator tickets --------------------------------------------------------

TICKET_LIST_DOCS = {
    "summary": "List my support tickets",
    "description": (
        "Returns the caller's own tickets, newest first, paginated.\n\n"
        "Called when the Help > Tickets tab loads. Filter with `status` and "
        "`priority`; page with `page` and `size`.\n\n"
        + _SIGNED_IN_AUTH
        + "**Prerequisites:** None.\n\n"
        "**Important:** `status` and `priority` must be upper-case enum "
        "values (e.g. `IN_PROGRESS`, `HIGH`); anything else is 400. The ticket's subject is in "
        "`title` and its description in `message`."
    ),
    "tags": [TICKETS_TAG],
    "responses": {
        200: _ok(
            SupportRequestSerializer(many=True),
            "A page of the caller's tickets.",
            "Success",
            _page([_TICKET_EXAMPLE]),
        ),
        **_bad_request(
            "`status` or `priority` is not a known value.",
            {
                "Unknown status": (
                    "invalid_choice",
                    "Select a valid choice. done is not one of the available choices.",
                    "status",
                )
            },
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **_INVALID_PAGE,
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

TICKET_CREATE_DOCS = {
    "summary": "Create a support ticket",
    "description": (
        "Files a ticket, notifies Support staff in-app and emails the "
        "submitter a confirmation so they can track its progress.\n\n"
        "Called from the Support page's 'Create ticket' action → 'Submit a "
        "ticket' form (Subject, Email, Priority, Description). On 201 show "
        "the 'Ticket created' modal.\n\n"
        + _SIGNED_IN_AUTH
        + "**Prerequisites:** None.\n\n"
        "**Important:** The body differs from the appeal form — no title or "
        "web link. The response uses the shared support-request shape, so "
        "`subject` comes back as `title` and `description` as `message`. "
        "The confirmation goes to the `email` typed on the form, which need "
        "not match the account email."
    ),
    "tags": [TICKETS_TAG],
    "request": SupportTicketCreateSerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request",
            request_only=True,
            value={
                "subject": "KYC failure during log in",
                "email": "ada.obi@example.com",
                "priority": "HIGH",
                "description": (
                    "I have been experiencing repeated KYC failure. Please can "
                    "this be rectified so I can complete my registration."
                ),
            },
        )
    ],
    "responses": {
        201: _ok(
            SupportRequestSerializer,
            "Ticket created.",
            "Created",
            {**_TICKET_EXAMPLE, "status": "OPEN", "assigned_to_name": None},
        ),
        **_bad_request(
            "A required field is missing, or `priority` is not a known level.",
            {
                "Missing field": ("required", "This field is required.", "subject"),
                "Unknown priority": (
                    "invalid_choice",
                    '"URGENT" is not a valid choice.',
                    "priority",
                ),
            },
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

TICKET_RETRIEVE_DOCS = {
    "summary": "Retrieve my ticket",
    "description": (
        "One of the caller's own tickets with its conversation thread — "
        "replies and 'Ticket status changed to …' entries, oldest first.\n\n"
        "Called when the creator opens a ticket from Help > Tickets.\n\n"
        + _SIGNED_IN_AUTH
        + "**Prerequisites:** The ticket must exist and belong to the "
        "caller.\n\n"
        "**Important:** The ticket's own `message` is the opening bubble; "
        "`messages` holds only what came after. Place bubbles by "
        "`from_support`, and label the caller's own as 'You' with "
        "`is_mine`. Another user's ticket returns 404, not 403."
    ),
    "tags": [TICKETS_TAG],
    "responses": {
        200: _ok(
            SupportTicketDetailSerializer,
            "The ticket with its thread.",
            "Success",
            {
                **_TICKET_DETAIL_EXAMPLE,
                "messages": [
                    {**_STATUS_CHANGE_MESSAGE, "is_mine": False},
                    {**_SUPPORT_REPLY_MESSAGE, "is_mine": False},
                    _CREATOR_REPLY_MESSAGE,
                ],
            },
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **_TICKET_NOT_FOUND,
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

TICKET_REPLY_DOCS = {
    "summary": "Reply to my ticket",
    "description": (
        "Adds the caller's reply to their own ticket's thread and notifies "
        "the assigned support officer in-app — or every Support staff "
        "member if no officer is assigned yet.\n\n"
        "Called from 'Add a response to this ticket' on the creator's ticket "
        "view.\n\n"
        + _SIGNED_IN_AUTH
        + "**Prerequisites:** The ticket must belong to the caller and not "
        "be `CLOSED`.\n\n"
        "**Important:** Text only — attachments are not supported. The "
        "response is the new thread entry; append it rather than "
        "re-fetching the ticket."
    ),
    "tags": [TICKETS_TAG],
    "request": SupportTicketReplySerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request", request_only=True, value={"body": "Ok. Thanks"}
        )
    ],
    "responses": {
        201: _ok(
            SupportTicketMessageSerializer,
            "Reply added.",
            "Created",
            _CREATOR_REPLY_MESSAGE,
        ),
        **_REPLY_ERRORS,
        **STANDARD_ERROR_RESPONSES["auth"],
        **_TICKET_NOT_FOUND,
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

# --- Creator appeals --------------------------------------------------------

APPEAL_LIST_DOCS = {
    "summary": "List my appeals",
    "description": (
        "Returns the caller's own appeals, newest first, paginated, with "
        "each one's status and `due_at` deadline.\n\n"
        "Called wherever the creator reviews appeals they have filed.\n\n"
        + _SIGNED_IN_AUTH
        + "**Prerequisites:** None.\n\n"
        "**Important:** Appeals only move `OPEN` → `RESOLVED`; the decision "
        "is final."
    ),
    "tags": [APPEALS_TAG],
    "responses": {
        200: _ok(
            SupportRequestSerializer(many=True),
            "A page of the caller's appeals.",
            "Success",
            _page([_APPEAL_EXAMPLE]),
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **_INVALID_PAGE,
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

APPEAL_CREATE_DOCS = {
    "summary": "Request an appeal",
    "description": (
        "Files a written appeal against a rejection or suspension. Notifies "
        "Support staff in-app, logs it to the caller's activity history and "
        "sets `due_at` to 7 business days out (the dispute-resolution "
        "SLA).\n\n"
        "Called from the Support page's 'Request appeal' action → 'Request "
        "for an appeal' form (Title, Email, Web link, Description). On 201 "
        "show the 'Submission successful' modal.\n\n"
        "**Auth:** Any signed-in user (Bearer token) — including a suspended "
        "creator, who can still sign in to appeal.\n\n"
        "**Prerequisites:** None.\n\n"
        "**Important:** `web_link` must be a full URL — prepend the form's "
        "`https://` prefix before sending. This is independent of "
        "`POST /course-appeals/`, which disputes one specific rejected "
        "course."
    ),
    "tags": [APPEALS_TAG],
    "request": SupportTitledRequestCreateSerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request",
            request_only=True,
            value={
                "title": "My account was suspended in error",
                "email": "ada.obi@example.com",
                "web_link": "https://adaobi.dev/portfolio",
                "description": (
                    "The flagged lesson was my original work; please review it."
                ),
            },
        ),
        OpenApiExample(
            name="Without web link",
            request_only=True,
            value={
                "title": "Course rejected for plagiarism",
                "email": "ada.obi@example.com",
                "description": "I wrote every module myself and can share drafts.",
            },
        ),
    ],
    "responses": {
        201: _ok(
            SupportRequestSerializer,
            "Appeal filed.",
            "Created",
            _APPEAL_EXAMPLE,
        ),
        **_bad_request(
            "A required field is missing, or `web_link` is not a full URL.",
            {
                "Missing field": ("required", "This field is required.", "title"),
                "Bad web link": ("invalid", "Enter a valid URL.", "web_link"),
            },
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

# --- Admin: all support requests -------------------------------------------

_KIND_PARAM = OpenApiParameter(
    name="kind",
    description="Only this kind of request (case-insensitive).",
    required=False,
    type=str,
    enum=SupportRequestKind.values,
)
_STATUS_PARAM = OpenApiParameter(
    name="status",
    description="Only requests in this status (case-insensitive).",
    required=False,
    type=str,
    enum=SupportRequestStatus.values,
)

ADMIN_REQUEST_LIST_DOCS = {
    "summary": "List support requests (staff queue)",
    "description": (
        "Every contact message, ticket and appeal, newest first, "
        "paginated.\n\n"
        "Called by the staff support queue. Use the Tickets endpoints for "
        "the admin Tickets page; this queue is mainly for contact messages "
        "and appeals.\n\n" + _ADMIN_AUTH + "**Prerequisites:** None.\n\n"
        "**Important:** `kind` and `status` are case-insensitive; an "
        "unknown value returns an empty page, not an error."
    ),
    "tags": [ADMIN_REQUESTS_TAG],
    "parameters": [_KIND_PARAM, _STATUS_PARAM],
    "responses": {
        200: _ok(
            SupportRequestSerializer(many=True),
            "A page of support requests.",
            "Success",
            _page([_APPEAL_EXAMPLE, _CONTACT_EXAMPLE]),
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **_INVALID_PAGE,
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

ADMIN_REQUEST_RETRIEVE_DOCS = {
    "summary": "Retrieve a support request",
    "description": (
        "One contact message, ticket or appeal.\n\n"
        "Called when staff open an item from the support queue.\n\n"
        + _ADMIN_AUTH
        + "**Prerequisites:** The request must exist.\n\n"
        "**Important:** For a ticket's conversation thread use the admin "
        "ticket detail endpoint instead."
    ),
    "tags": [ADMIN_REQUESTS_TAG],
    "responses": {
        200: _ok(SupportRequestSerializer, "The request.", "Success", _APPEAL_EXAMPLE),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **_not_found("No support request with this ID."),
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

ADMIN_REQUEST_RESOLVE_DOCS = {
    "summary": "Resolve a support request",
    "description": (
        "Marks an Open request Resolved with optional notes and notifies the "
        "submitter in-app.\n\n"
        "Called when staff close out a contact message or decide an "
        "appeal.\n\n"
        + _ADMIN_AUTH
        + "**Prerequisites:** The request must be `OPEN`.\n\n"
        "**Important:** Final — a resolved request cannot be resolved again "
        "or reopened. For tickets prefer the ticket status endpoint, which "
        "also records the change in the thread."
    ),
    "tags": [ADMIN_REQUESTS_TAG],
    "request": SupportResolveSerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request",
            request_only=True,
            value={"notes": "Reviewed the flagged lesson; suspension lifted."},
        )
    ],
    "responses": {
        200: _ok(
            SupportRequestSerializer,
            "The resolved request.",
            "Resolved",
            {
                **_APPEAL_EXAMPLE,
                "status": "RESOLVED",
                "resolution_notes": "Reviewed the flagged lesson; suspension lifted.",
                "resolved_at": "2026-10-20T10:00:00Z",
            },
        ),
        **_bad_request(
            "The request is not Open.",
            {
                "Not open": (
                    "invalid",
                    "Request cannot be resolved from status 'RESOLVED'.",
                    None,
                )
            },
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **_not_found("No support request with this ID."),
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

# --- Admin: Tickets page ----------------------------------------------------

ADMIN_TICKET_LIST_DOCS = {
    "summary": "List tickets",
    "description": (
        "Every ticket, newest first, paginated, with the submitter's name, "
        "subject, status and priority for each table row.\n\n"
        "Called when the admin Tickets page loads and whenever its search "
        "box, Status, Priority Level or Date filters change.\n\n"
        + _ADMIN_AUTH
        + "**Prerequisites:** None.\n\n"
        "**Important:** `search` matches any part of the ticket ID, the "
        "subject, the email and the submitter's name. `status` and "
        "`priority` are case-insensitive; an unknown value returns an "
        "empty page, not an error. `date_from`/`date_to` are inclusive "
        "`YYYY-MM-DD` dates on the created date. `ordering` takes "
        "`created_datetime`, `updated_datetime`, `priority` or `status`, "
        "prefixed with `-` for descending; `priority` sorts Low → Medium → "
        "High and `status` follows the lifecycle (Open → In-progress → "
        "Resolved → Closed), not the alphabet. The summary cards come from "
        "the stats endpoint, not this list."
    ),
    "tags": [ADMIN_TICKETS_TAG],
    "responses": {
        200: _ok(
            SupportRequestSerializer(many=True),
            "A page of tickets.",
            "Success",
            _page([_TICKET_EXAMPLE]),
        ),
        **_bad_request(
            "`date_from` or `date_to` is not a valid date.",
            {"Bad date": ("invalid", "Enter a valid date.", "date_from")},
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **_INVALID_PAGE,
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

ADMIN_TICKET_RETRIEVE_DOCS = {
    "summary": "Retrieve a ticket",
    "description": (
        "The ticket with its conversation thread (replies and status "
        "changes, oldest first), support officer and 'Created By' "
        "block.\n\n"
        "Called when an admin opens a row on the Tickets page (the "
        "conversation and Ticket Overview panel).\n\n"
        + _ADMIN_AUTH
        + "**Prerequisites:** The ticket must exist.\n\n"
        "**Important:** The ticket's own `message` is the opening bubble. "
        "Use `from_support` for the bubble's side and `is_mine` for the "
        "'You' label — a colleague's reply is `from_support` but not "
        "`is_mine`. Support officer is `assigned_to_name`; Last updated is "
        "`updated_datetime`."
    ),
    "tags": [ADMIN_TICKETS_TAG],
    "responses": {
        200: _ok(
            SupportTicketDetailSerializer,
            "The ticket with its thread.",
            "Success",
            _TICKET_DETAIL_EXAMPLE,
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **_ADMIN_TICKET_NOT_FOUND,
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

ADMIN_TICKET_STATS_DOCS = {
    "summary": "Get ticket status counts",
    "description": (
        "The four summary cards on the Tickets page: Opened, In-Progress, "
        "Resolved and Closed.\n\n"
        "Called when the Tickets page loads and after any status change.\n\n"
        + _ADMIN_AUTH
        + "**Prerequisites:** None.\n\n"
        "**Important:** Counts every ticket and ignores the list's search "
        "and filters."
    ),
    "tags": [ADMIN_TICKETS_TAG],
    "responses": {
        200: _ok(
            SupportTicketStatusCountsSerializer,
            "Ticket counts by status.",
            "Success",
            {"open": 3, "in_progress": 4, "resolved": 4, "closed": 2},
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

_TRANSITION_ERRORS = {
    "Same status": ("invalid", "Ticket is already 'Closed'.", "status"),
    "Not allowed": (
        "invalid",
        "Ticket cannot move from 'Closed' to 'In-progress'.",
        "status",
    ),
    "Open not settable": (
        "invalid_choice",
        '"OPEN" is not a valid choice.',
        "status",
    ),
}

ADMIN_TICKET_STATUS_DOCS = {
    "summary": "Change a ticket's status",
    "description": (
        "Moves one ticket to a new status, adds a 'Ticket status changed to "
        "…' entry to its thread, makes the caller the support officer if "
        "none is set, and notifies the submitter in-app and by email.\n\n"
        "Called from the Ticket Overview actions: 'Close ticket' (`CLOSED`), "
        "'Mark as in-progress' (`IN_PROGRESS`) and 'Mark as resolved' "
        "(`RESOLVED`).\n\n"
        + _ADMIN_AUTH
        + "**Prerequisites:** The move must be allowed — Open → any; "
        "In-progress → Resolved or Closed; Resolved → In-progress or "
        "Closed.\n\n"
        "**Important:** `CLOSED` is final: the ticket can't change status or "
        "take replies again. `OPEN` can't be set; it is only the starting "
        "state."
    ),
    "tags": [ADMIN_TICKETS_TAG],
    "request": SupportTicketStatusSerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request", request_only=True, value={"status": "IN_PROGRESS"}
        )
    ],
    "responses": {
        200: _ok(
            SupportRequestSerializer,
            "The ticket in its new status.",
            "Success",
            _TICKET_EXAMPLE,
        ),
        **_bad_request("The status move is not allowed.", _TRANSITION_ERRORS),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **_ADMIN_TICKET_NOT_FOUND,
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

ADMIN_TICKET_BULK_STATUS_DOCS = {
    "summary": "Change many tickets' status",
    "description": (
        "Applies one status to every selected ticket, with the same rules "
        "and side effects as the single change.\n\n"
        "Called from the bulk bar that appears when rows are checked on the "
        "Tickets page ('Close ticket', 'Mark as in-progress', 'Mark as "
        "resolved').\n\n" + _ADMIN_AUTH + "**Prerequisites:** 1–100 ticket IDs.\n\n"
        "**Important:** Not all-or-nothing. A ticket that can't make the "
        "move (e.g. already Closed) or doesn't exist is returned in "
        "`skipped` with a reason; the rest are updated and returned in "
        "`updated`. Repeated IDs are processed once."
    ),
    "tags": [ADMIN_TICKETS_TAG],
    "request": SupportTicketBulkStatusSerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request",
            request_only=True,
            value={
                "ids": [
                    "fea458f1-ad9e-494d-8cea-2d4a6b8c0e13",
                    "9b8a7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                ],
                "status": "CLOSED",
            },
        )
    ],
    "responses": {
        200: _ok(
            SupportTicketBulkResultSerializer,
            "Which tickets changed and which were skipped.",
            "Success",
            {
                "updated": [{**_TICKET_EXAMPLE, "status": "CLOSED"}],
                "skipped": [
                    {
                        "id": "9b8a7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                        "reason": "Ticket is already 'Closed'.",
                    }
                ],
            },
        ),
        **_bad_request(
            "`ids` is empty or too long, or `status` is not settable.",
            {
                "No IDs": (
                    "min_length",
                    "Ensure this field has at least 1 elements.",
                    "ids",
                ),
                "Open not settable": _TRANSITION_ERRORS["Open not settable"],
            },
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

ADMIN_TICKET_REPLY_DOCS = {
    "summary": "Reply to a ticket",
    "description": (
        "Adds a support reply to the ticket's thread, makes the caller the "
        "support officer if none is set, and notifies the submitter in-app "
        "and by email.\n\n"
        "Called from 'Add a response to this ticket' on the admin ticket "
        "view.\n\n"
        + _ADMIN_AUTH
        + "**Prerequisites:** The ticket must not be `CLOSED`.\n\n"
        "**Important:** Text only — attachments are not supported. Replying "
        "does not change the ticket's status; use the status endpoint for "
        "that."
    ),
    "tags": [ADMIN_TICKETS_TAG],
    "request": SupportTicketReplySerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request",
            request_only=True,
            value={"body": _SUPPORT_REPLY_MESSAGE["body"]},
        )
    ],
    "responses": {
        201: _ok(
            SupportTicketMessageSerializer,
            "Reply added.",
            "Created",
            _SUPPORT_REPLY_MESSAGE,
        ),
        **_REPLY_ERRORS,
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **_ADMIN_TICKET_NOT_FOUND,
        **STANDARD_ERROR_RESPONSES["server"],
    },
}
