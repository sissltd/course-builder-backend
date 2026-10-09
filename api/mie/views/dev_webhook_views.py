from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.mie.authentication import MieDeveloperAuthentication
from api.mie.enums import WEBHOOK_ALL_EVENTS, WebhookEventType
from api.mie.permissions import IsMieDeveloper
from api.mie.serializers.webhook_endpoint_serializer import (
    WebhookEndpointSerializer,
    WebhookEndpointWriteSerializer,
    WebhookEventTypeSerializer,
)
from api.mie.services import webhook_endpoint_service
from api.mie.services.documentation_service import WEBHOOK_EVENT_DOCS
from api.mie.services.webhook_endpoint_service import MAX_WEBHOOK_ENDPOINTS
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

TAG = "Developer — MIE Webhooks"
AUTH = (
    "**Auth:** Requires a valid MIE developer API key (`X-MIE-Api-Key`) or "
    "a platform Bearer session token, so the same calls work from your code "
    "and from your developer profile."
)

ENDPOINT_ID_PARAMETER = OpenApiParameter(
    name="endpoint_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    description="The `id` of one of your webhook endpoints.",
)

ENDPOINT_EXAMPLE = {
    "id": "5b1f0c9e-2d4a-4f7e-9a3b-8c6d1e2f3a4b",
    "url": "https://hooks.studio.io/mie/courses",
    "events": ["COURSE_TEXT_APPROVED", "COURSE_REVISION_REQUESTED", "COURSE_PUBLISHED"],
    "created_datetime": "2026-10-08T09:00:00Z",
    "updated_datetime": "2026-10-08T09:00:00Z",
}


def _error(code: str, message: str, field_name=None) -> dict:
    return {
        "errors": [
            {
                "type": "client_error" if field_name is None else "validation_error",
                "code": code,
                "message": message,
                "field_name": field_name,
            }
        ]
    }


VALIDATION_400 = OpenApiResponse(
    description="A field is missing or invalid.",
    examples=[
        OpenApiExample(
            "Unknown event type",
            value=_error(
                "invalid",
                "Unknown event types: ['COURSE_DELETED']. Use \"all\" or any of [...].",
                "events",
            ),
        ),
        OpenApiExample(
            "all mixed with other events",
            value=_error(
                "invalid", '"all" already includes every event; send it on its own.', "events"
            ),
        ),
    ],
)
DUPLICATE_409 = OpenApiExample(
    "URL already registered",
    value=_error(
        "webhook_endpoint_duplicate",
        "You already have a webhook endpoint for this URL. Edit it instead.",
    ),
)


class MieWebhookEndpointListView(APIView):
    """List your webhook endpoints, or add one."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]

    @extend_schema(
        operation_id="mie_v1_webhooks_list",
        summary="List your webhook endpoints",
        description=(
            "Every URL your account receives webhooks on, with the events "
            "each one takes, oldest first.\n\n"
            "Called to show your webhook settings, or to check what is "
            "configured before changing it.\n\n"
            f"{AUTH}\n\n"
            "**Prerequisites:** None. Every account has at least one "
            "endpoint, created from the URL given at registration.\n\n"
            f"**Important:** At most {MAX_WEBHOOK_ENDPOINTS} endpoints, so "
            "the list is not paginated. `events` is "
            f'`["{WEBHOOK_ALL_EVENTS}"]` for an endpoint that takes every '
            "event."
        ),
        tags=[TAG],
        responses={
            200: OpenApiResponse(
                response=WebhookEndpointSerializer(many=True),
                description="Your endpoints.",
                examples=[OpenApiExample("Endpoints", value=[ENDPOINT_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        endpoints = webhook_endpoint_service.live_endpoints(developer=request.auth)
        return Response(WebhookEndpointSerializer(endpoints, many=True).data)

    @extend_schema(
        summary="Add a webhook endpoint",
        description=(
            "Adds a URL to receive webhooks on, and the events it should "
            "receive: every event, or the ones you choose.\n\n"
            "Called when you want events delivered to another service, or "
            "split across services (for example idea decisions to one URL "
            "and course review events to another).\n\n"
            f"{AUTH}\n\n"
            "**Prerequisites:** Fewer than "
            f"{MAX_WEBHOOK_ENDPOINTS} endpoints, and no live endpoint of "
            "yours with the same URL.\n\n"
            "**Important:** Applies to events recorded from now on. Every "
            "endpoint is signed with your one signing secret. Each endpoint "
            "gets its own delivery and retries, and its own `event_id` for an "
            "event: two endpoints that take the same event receive two ids."
        ),
        tags=[TAG],
        request=WebhookEndpointWriteSerializer,
        examples=[
            OpenApiExample(
                "Course events only",
                request_only=True,
                value={
                    "url": ENDPOINT_EXAMPLE["url"],
                    "events": ENDPOINT_EXAMPLE["events"],
                },
            ),
            OpenApiExample(
                "Every event",
                request_only=True,
                value={"url": "https://hooks.studio.io/mie/all", "events": [WEBHOOK_ALL_EVENTS]},
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=WebhookEndpointSerializer,
                description="Endpoint added.",
                examples=[OpenApiExample("Created", value=ENDPOINT_EXAMPLE, response_only=True)],
            ),
            400: VALIDATION_400,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            409: OpenApiResponse(
                description="The URL is already one of your endpoints, or you are at the limit.",
                examples=[
                    DUPLICATE_409,
                    OpenApiExample(
                        "Limit reached",
                        value=_error(
                            "webhook_endpoint_limit",
                            f"You already have {MAX_WEBHOOK_ENDPOINTS} webhook endpoints, "
                            "the most an account can keep. Delete or edit one instead.",
                        ),
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = WebhookEndpointWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        endpoint = webhook_endpoint_service.create_endpoint(
            developer=request.auth, **serializer.validated_data
        )
        return Response(WebhookEndpointSerializer(endpoint).data, status=status.HTTP_201_CREATED)


class MieWebhookEndpointDetailView(APIView):
    """Read, change or delete one of your webhook endpoints."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]

    @extend_schema(
        operation_id="mie_v1_webhooks_retrieve",
        summary="Show one webhook endpoint",
        description=(
            "One of your endpoints and the events it takes.\n\n"
            "Called before editing an endpoint.\n\n"
            f"{AUTH}\n\n"
            "**Prerequisites:** The endpoint is yours and not deleted.\n\n"
            "**Important:** Another developer's endpoint is a 404, like one "
            "that does not exist."
        ),
        tags=[TAG],
        parameters=[ENDPOINT_ID_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=WebhookEndpointSerializer,
                description="The endpoint.",
                examples=[OpenApiExample("Endpoint", value=ENDPOINT_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, endpoint_id):
        endpoint = webhook_endpoint_service.get_endpoint(
            developer=request.auth, endpoint_id=endpoint_id
        )
        return Response(WebhookEndpointSerializer(endpoint).data)

    @extend_schema(
        summary="Change a webhook endpoint",
        description=(
            "Changes an endpoint's URL, the events it takes, or both. Send "
            "only what changes. You can change it as often as you like.\n\n"
            "Called to move an endpoint to a new URL, or to widen or narrow "
            "the events it receives.\n\n"
            f"{AUTH}\n\n"
            "**Prerequisites:** The endpoint is yours and not deleted; a new "
            "URL is not already another of your endpoints.\n\n"
            "**Important:** `events` replaces the whole list; it is not "
            "merged. Applies to events recorded from now on: deliveries "
            "already queued still go to this endpoint, at its current URL."
        ),
        tags=[TAG],
        parameters=[ENDPOINT_ID_PARAMETER],
        request=WebhookEndpointWriteSerializer,
        examples=[
            OpenApiExample(
                "Take every event",
                request_only=True,
                value={"events": [WEBHOOK_ALL_EVENTS]},
            ),
            OpenApiExample(
                "Move to a new URL",
                request_only=True,
                value={"url": "https://hooks.studio.io/mie/v2"},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=WebhookEndpointSerializer,
                description="The endpoint after the change.",
                examples=[OpenApiExample("Updated", value=ENDPOINT_EXAMPLE, response_only=True)],
            ),
            400: VALIDATION_400,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            409: OpenApiResponse(
                description="The new URL is already one of your endpoints.",
                examples=[DUPLICATE_409],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def patch(self, request, endpoint_id):
        serializer = WebhookEndpointWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        endpoint = webhook_endpoint_service.update_endpoint(
            developer=request.auth, endpoint_id=endpoint_id, **serializer.validated_data
        )
        return Response(WebhookEndpointSerializer(endpoint).data)

    @extend_schema(
        summary="Delete a webhook endpoint",
        description=(
            "Stops sending events to an endpoint.\n\n"
            "Called when a receiving service is retired.\n\n"
            f"{AUTH}\n\n"
            "**Prerequisites:** The endpoint is yours, and you have at least "
            "one other: webhooks are the only way we tell you about "
            "decisions, so an account always keeps one.\n\n"
            "**Important:** Deliveries still queued for this endpoint are "
            "dropped, not moved to another one. To keep receiving at a new "
            "address, change the URL instead of deleting."
        ),
        tags=[TAG],
        parameters=[ENDPOINT_ID_PARAMETER],
        request=None,
        responses={
            204: OpenApiResponse(description="Deleted."),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            409: OpenApiResponse(
                description="It is your only endpoint.",
                examples=[
                    OpenApiExample(
                        "Only endpoint",
                        value=_error(
                            "last_webhook_endpoint",
                            "This is your only webhook endpoint. Change its URL or "
                            "events instead of deleting it.",
                        ),
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def delete(self, request, endpoint_id):
        webhook_endpoint_service.delete_endpoint(developer=request.auth, endpoint_id=endpoint_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MieWebhookEventTypesView(APIView):
    """The event types an endpoint can subscribe to."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]

    @extend_schema(
        summary="List the webhook event types",
        description=(
            "Every event type you can put in an endpoint's `events`, with "
            "what makes it fire.\n\n"
            "Called to build the event picker for an endpoint.\n\n"
            f"{AUTH}\n\n"
            "**Prerequisites:** None.\n\n"
            f'**Important:** `["{WEBHOOK_ALL_EVENTS}"]` is not listed here: '
            "it is the shortcut for every type below, including types added "
            "later."
        ),
        tags=[TAG],
        responses={
            200: OpenApiResponse(
                response=WebhookEventTypeSerializer(many=True),
                description="Every event type, in catalogue order.",
                examples=[
                    OpenApiExample(
                        "Event types",
                        value=[
                            {
                                "event": WebhookEventType.SUBMISSION_APPROVED.value,
                                "label": WebhookEventType.SUBMISSION_APPROVED.label,
                                "fires_when": "A superadmin approved the idea.",
                            }
                        ],
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        return Response(
            WebhookEventTypeSerializer(
                [
                    {
                        "event": event_type.value,
                        "label": event_type.label,
                        "fires_when": WEBHOOK_EVENT_DOCS[event_type]["fires_when"],
                    }
                    for event_type in WebhookEventType
                ],
                many=True,
            ).data
        )
