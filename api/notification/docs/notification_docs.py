from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    inline_serializer,
)
from rest_framework import serializers

from api.notification.serializers import NotificationReadSerializer
from includes.spectacular.responses import (
    STANDARD_ERROR_RESPONSES,
    inline_success_response,
)

NotificationListItemSerializer = inline_serializer(
    name="NotificationListItem",
    fields={
        "id": serializers.UUIDField(),
        "title": serializers.CharField(),
        "content": serializers.CharField(),
        "content_type": serializers.CharField(),
        "is_read": serializers.BooleanField(),
        "created_datetime": serializers.DateTimeField(),
        "metadata": serializers.DictField(required=False),
    },
)


NotificationListPaginatorSerializer = inline_serializer(
    name="NotificationListPaginator",
    fields={
        "next": serializers.URLField(allow_null=True),
        "previous": serializers.URLField(allow_null=True),
    },
)


NotificationListDataSerializer = inline_serializer(
    name="NotificationListData",
    fields={
        "paginator": NotificationListPaginatorSerializer,
        "results": serializers.ListSerializer(child=NotificationListItemSerializer),
    },
)


NotificationListSuccessSerializer = inline_serializer(
    name="NotificationListSuccess",
    fields={
        "status": serializers.BooleanField(),
        "message": serializers.CharField(),
        "data": NotificationListDataSerializer,
    },
)

NOTIFICATION_READ_TOGGLE_DOCS = {
    "summary": "Toggle a notification read status",
    "description": "Marks one of the current user's in-app notifications as read or "
    "unread. The frontend uses this after the user interacts with a "
    "single notification item so it can update badge counts and read "
    "state without re-fetching the full list.\n\n"
    "Called from the notifications drawer or notifications page when "
    "a user toggles a notification's read state.\n\n"
    "**Auth:** Any authenticated user.\n\n"
    "**Prerequisites:** The notification must belong to the current "
    "authenticated user.\n\n"
    "**Important:** This endpoint updates exactly one notification per "
    "request. If the notification does not exist for this user, the "
    "endpoint returns 404. \n\n"
    "The action is idempotent: marking a notification as read when it "
    "is already read, or marking it as unread when it is already unread, will not cause an error.",
    "tags": ["Users — Notifications"],
    "request": NotificationReadSerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request",
            request_only=True,
            value={
                "notification_id": "f26ee285-6d9d-4e88-a939-4a246dcb8127",
                "read_status": True,
            },
        )
    ],
    "responses": {
        200: inline_success_response(
            description="Notification read status updated successfully.",
            examples=[
                OpenApiExample(
                    name="Marked as read",
                    response_only=True,
                    value={
                        "status": 200,
                        "success": True,
                        "message": "Notification f26ee285-6d9d-4e88-a939-4a246dcb8127 marked as read.",
                    },
                ),
            ],
        ),
        404: OpenApiResponse(
            description=(
                "The notification does not exist or does not belong to the current user."
            ),
            examples=[
                OpenApiExample(
                    name="Notification not found",
                    value={
                        "errors": [
                            {
                                "type": "client_error",
                                "code": "not_found",
                                "message": "Notification 1284 not found for user 97.",
                                "field_name": None,
                            }
                        ]
                    },
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["validation"],
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}


NOTIFICATION_STREAM_DOCS = {
    "summary": "🔴Stream real-time notifications via SSE",
    "description": (
        "### ⚠️ WARNING: DO NOT USE THE SWAGGER 'TRY IT OUT' BUTTON FOR THIS ENDPOINT.\n\n"
        "Opens a persistent Server-Sent Events (SSE) connection that pushes "
        "in-app notifications to the authenticated user in real-time. On first "
        "connection the last 20 notifications are sent immediately; after that "
        "new notifications are pushed as they arrive. The connection stays open "
        "until the client disconnects.\n\n"
        "Connect once when the user's session starts (e.g. after login or on "
        "the main dashboard mount). Re-connect with exponential back-off on "
        "unexpected closure. Use the `/api/v1/users/me/notifications/` endpoint to fetch "
        "historical notifications beyond the initial 20.\n\n"
        "**Auth:** Any authenticated user — Bearer token required in the "
        "`Authorization` header.\n\n"
        "**Prerequisites:** The caller must hold a valid Bearer token. Tokens "
        "passed as query parameters are not supported.\n\n"
        "**Important:** This endpoint returns `Content-Type: text/event-stream` "
        "— it is **not** a standard JSON response. The Swagger UI *Try it out* "
        "button does not support SSE; test with `curl` or `Postman's SSE` "
        "client instead. Each event is a JSON-encoded notification object. "
        "The connection does not time out on the server side; the client must "
        "handle reconnection."
    ),
    "tags": ["Users — Notifications"],
    "responses": {
        200: OpenApiResponse(
            description=(
                "SSE stream opened. Each event contains a JSON-encoded "
                "notification object with the fields shown in the example."
            ),
            examples=[
                OpenApiExample(
                    name="SSE event payload",
                    value={
                        "id": "f26ee285-6d9d-4e88-a939-4a246dcb8127",
                        "title": "Course approved",
                        "content": 'Your course "Backend Fundamentals" has been approved.',
                        "content_type": "text",
                        "is_read": False,
                        "created_datetime": "2026-08-01T10:30:00.000000Z",
                        "metadata": {
                            "course_id": "af470cc5-1ec9-458f-9fc9-66da0bcadf44",
                            "action": "course.approved",
                        },
                    },
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}


NOTIFICATION_LIST_DOCS = {
    "summary": "List in-app notifications",
    "description": "Returns the authenticated user's in-app notifications in "
    "reverse chronological order using cursor pagination. This powers "
    "the notifications center and allows the client to fetch history "
    "beyond the initial SSE payload. Results can be filtered to only "
    "read or unread notifications via the `is_read` query parameter.\n\n"
    "Called when opening the notifications list screen, or when the "
    "client needs to load older notifications while paginating.\n\n"
    "**Auth:** Any authenticated user.\n\n"
    "**Prerequisites:** The caller must provide a valid Bearer token.\n\n"
    "**Important:** This endpoint returns only in-app notifications "
    "belonging to the authenticated user. Pagination is cursor-based "
    "and sorted by newest first (`-created_datetime`, `-id`) to keep "
    "the order stable when timestamps are equal.",
    "tags": ["Users — Notifications"],
    "parameters": [
        OpenApiParameter(
            name="is_read",
            type=bool,
            location=OpenApiParameter.QUERY,
            required=False,
            description="Optional filter. `true` returns only read notifications and `false` returns only unread notifications.",
        ),
        OpenApiParameter(
            name="cursor",
            type=str,
            location=OpenApiParameter.QUERY,
            required=False,
            description="Opaque cursor returned by the previous page response.",
        ),
        OpenApiParameter(
            name="size",
            type=int,
            location=OpenApiParameter.QUERY,
            required=False,
            description="Optional page size for cursor pagination.",
        ),
    ],
    "responses": {
        200: OpenApiResponse(
            response=NotificationListSuccessSerializer,
            description="Notifications retrieved successfully.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value={
                        "status": True,
                        "message": "Successfully retrieved data",
                        "data": {
                            "paginator": {
                                "next": "https://api.example.com/api/v1/users/me/notifications/?cursor=cD0yMDI2LTA4LTAxKzEwJTNBMzAlM0EwMC4wMDAwMDBa",
                                "previous": None,
                            },
                            "results": [
                                {
                                    "id": "f26ee285-6d9d-4e88-a939-4a246dcb8127",
                                    "title": "Course approved",
                                    "content": 'Your course "Backend Fundamentals" has been approved.',
                                    "content_type": "text",
                                    "is_read": False,
                                    "created_datetime": "2026-08-01T10:30:00.000000Z",
                                    "metadata": {
                                        "course_id": "af470cc5-1ec9-458f-9fc9-66da0bcadf44",
                                        "action": "course.approved",
                                    },
                                }
                            ],
                        },
                    },
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}
