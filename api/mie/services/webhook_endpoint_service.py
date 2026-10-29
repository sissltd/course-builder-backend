"""A developer's webhook endpoints, and the fan-out that records events for them.

A developer receives webhooks on one or more endpoints. Each endpoint takes
every event type (`all_events`) or a chosen list. Recording an event writes
one WebhookEvent row per live endpoint that takes its type, and the
dispatcher delivers each row to its own endpoint, so one failing URL never
holds back another.

Every place that announces something to a developer goes through
record_events: it reads the endpoints of every developer involved in one
query and writes every row in one bulk insert, whatever the number of
events or endpoints.
"""

from collections import defaultdict

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework import exceptions

from api.mie.enums import WEBHOOK_ALL_EVENTS, WebhookDeliveryStatus, WebhookEventType
from api.mie.exceptions import (
    LastWebhookEndpoint,
    WebhookEndpointDuplicate,
    WebhookEndpointLimitReached,
)
from api.mie.models import CourseSubmission, DeveloperAccount, WebhookEndpoint, WebhookEvent

MAX_WEBHOOK_ENDPOINTS = 10
"""Live endpoints one developer account may keep."""

ENDPOINT_REMOVED_ERROR = "webhook endpoint deleted"
"""last_error on deliveries that were still pending when their endpoint went."""


def live_endpoints(*, developer: DeveloperAccount) -> QuerySet[WebhookEndpoint]:
    """The developer's endpoints that receive events, oldest first."""

    return WebhookEndpoint.objects.filter(developer=developer, is_deleted=False)


def get_endpoint(*, developer: DeveloperAccount, endpoint_id) -> WebhookEndpoint:
    """One of the developer's live endpoints. Another developer's, or a
    deleted one, is a 404 like one that never existed."""

    endpoint = live_endpoints(developer=developer).filter(id=endpoint_id).first()
    if endpoint is None:
        raise exceptions.NotFound("Webhook endpoint not found.")
    return endpoint


def events_for(endpoint: WebhookEndpoint) -> list[str]:
    """The endpoint's subscription as the API states it: ["all"], or the
    event types it takes in catalogue order."""

    if endpoint.all_events:
        return [WEBHOOK_ALL_EVENTS]
    return [value for value in WebhookEventType.values if value in endpoint.event_types]


def _subscription(events: list[str]) -> dict:
    """Model fields for a validated `events` list (see the serializer)."""

    if events == [WEBHOOK_ALL_EVENTS]:
        return {"all_events": True, "event_types": []}
    return {"all_events": False, "event_types": events}


def _lock_developer(developer: DeveloperAccount) -> None:
    """Serialise endpoint changes for one developer, so two concurrent
    requests cannot both pass the limit or both delete "the last but one"."""

    DeveloperAccount.objects.select_for_update().filter(pk=developer.pk).first()


def _refuse_duplicate_url(*, developer: DeveloperAccount, url: str, exclude_id=None) -> None:
    taken = live_endpoints(developer=developer).filter(url=url)
    if exclude_id is not None:
        taken = taken.exclude(id=exclude_id)
    if taken.exists():
        raise WebhookEndpointDuplicate()


def add_first_endpoint(*, developer: DeveloperAccount, url: str) -> WebhookEndpoint:
    """The endpoint a registration creates: the URL the developer gave,
    taking every event. Called inside the account-creating transaction."""

    return WebhookEndpoint.objects.create(developer=developer, url=url, all_events=True)


def create_endpoint(*, developer: DeveloperAccount, url: str, events: list[str]) -> WebhookEndpoint:
    """Add an endpoint. Raises WebhookEndpointLimitReached (409) past
    MAX_WEBHOOK_ENDPOINTS and WebhookEndpointDuplicate (409) for a URL the
    developer already has."""

    with transaction.atomic():
        _lock_developer(developer)
        if live_endpoints(developer=developer).count() >= MAX_WEBHOOK_ENDPOINTS:
            raise WebhookEndpointLimitReached(
                f"You already have {MAX_WEBHOOK_ENDPOINTS} webhook endpoints, "
                "the most an account can keep. Delete or edit one instead."
            )
        _refuse_duplicate_url(developer=developer, url=url)
        return WebhookEndpoint.objects.create(
            developer=developer, url=url, **_subscription(events)
        )


def update_endpoint(
    *,
    developer: DeveloperAccount,
    endpoint_id,
    url: str | None = None,
    events: list[str] | None = None,
) -> WebhookEndpoint:
    """Change an endpoint's URL, its events, or both. Applies to events
    recorded from now on; deliveries already queued keep their endpoint."""

    with transaction.atomic():
        _lock_developer(developer)
        endpoint = get_endpoint(developer=developer, endpoint_id=endpoint_id)
        update_fields = ["updated_datetime"]
        if url is not None and url != endpoint.url:
            _refuse_duplicate_url(developer=developer, url=url, exclude_id=endpoint.id)
            endpoint.url = url
            update_fields.append("url")
        if events is not None:
            for field, value in _subscription(events).items():
                setattr(endpoint, field, value)
                update_fields.append(field)
        endpoint.save(update_fields=update_fields)
        return endpoint


def delete_endpoint(*, developer: DeveloperAccount, endpoint_id) -> None:
    """Soft-delete an endpoint and fail its undelivered events, which now
    have nowhere to go. Raises LastWebhookEndpoint (409) for the only one."""

    with transaction.atomic():
        _lock_developer(developer)
        endpoint = get_endpoint(developer=developer, endpoint_id=endpoint_id)
        if not live_endpoints(developer=developer).exclude(id=endpoint.id).exists():
            raise LastWebhookEndpoint()
        endpoint.delete()
        WebhookEvent.objects.filter(
            endpoint=endpoint, delivery_status=WebhookDeliveryStatus.PENDING
        ).update(
            delivery_status=WebhookDeliveryStatus.FAILED,
            last_error=ENDPOINT_REMOVED_ERROR,
            next_retry_at=None,
            updated_datetime=timezone.now(),
        )


def record_events(items: list[tuple[CourseSubmission, str, dict]]) -> list[WebhookEvent]:
    """Record each (submission, event_type, payload) for every live endpoint
    of its developer that takes that event type.

    Two queries however many events or endpoints: one read of the endpoints,
    one bulk insert. An event no endpoint takes records nothing.
    """

    if not items:
        return []
    developer_ids = {submission.developer_id for submission, _, _ in items}
    endpoints_by_developer = defaultdict(list)
    for endpoint in WebhookEndpoint.objects.filter(
        developer_id__in=developer_ids, is_deleted=False
    ):
        endpoints_by_developer[endpoint.developer_id].append(endpoint)

    rows = [
        WebhookEvent(
            submission=submission,
            endpoint=endpoint,
            event_type=event_type,
            payload=payload,
        )
        for submission, event_type, payload in items
        for endpoint in endpoints_by_developer[submission.developer_id]
        if endpoint.all_events or event_type in endpoint.event_types
    ]
    return WebhookEvent.objects.bulk_create(rows)


def record_event(*, submission: CourseSubmission, event_type: str, payload: dict) -> list[WebhookEvent]:
    """record_events for a single event."""

    return record_events([(submission, event_type, payload)])

