"""Outbound webhook delivery for MIE events.

Design contract
---------------
*Record-then-sweep.* Transition code only writes a WebhookEvent row
(submission_service.record_event); this module is the sole sender.

One dispatch pass costs O(N) in due events:

1. ONE indexed query fetches every due event plus its submission and the
   developer's webhook_url/signing secret (select_related - no N+1).
2. HTTP POSTs run concurrently on a small thread pool; no database work
   happens inside threads.
3. Outcomes are written back in two bulk updates (events then nothing
   else), regardless of N.

Scheduling needs zero extra infrastructure: call `dispatch_due_events`
from a single periodic job (celery beat entry or cron'd management
command `dispatch_mie_webhooks`). Events recorded while an account is
suspended stay PENDING and are delivered if the account returns;
permanently rejected accounts' events are dropped to FAILED so they stop
accumulating.

Signing scheme (mirror-image of how we'd want to be verified):

    X-MIE-Timestamp  unix seconds at send time
    X-MIE-Signature  hex HMAC-SHA256( f"{timestamp}.{raw_body}", secret )

Receivers recompute over the raw bytes and reject timestamps older than
REPLAY_WINDOW_SECONDS.
"""

import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import httpx
from django.db import models
from django.utils import timezone

from api.mie.enums import DeveloperAccountStatus, WebhookDeliveryStatus
from api.mie.models import WebhookEvent

MAX_ATTEMPTS = 5
"""Total delivery attempts before an event is terminally FAILED."""

RETRY_DELAYS_SECONDS = (60, 300, 900, 3600)
"""Backoff before attempt n+1 = RETRY_DELAYS_SECONDS[min(attempts-1, len-1)]."""

CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 10

DELIVERY_BATCH_SIZE = 200
"""bulk_update chunk size; keeps each UPDATE statement well-bounded."""

MAX_WORKERS = 8
"""Concurrent in-flight POSTs per pass - modest by design."""

REPLAY_WINDOW_SECONDS = 300
"""Documented to receivers; enforced on their side."""


@dataclass
class DispatchReport:
    """What one pass did; returned so callers (and logs) can be precise.

    Aggregated mutably while applying outcomes, then treated as immutable.
    """

    delivered: int = 0
    retried: int = 0
    failed: int = 0
    skipped_inactive: int = 0

    def __str__(self):
        return (
            f"delivered={self.delivered} retried={self.retried} "
            f"failed={self.failed} skipped_inactive={self.skipped_inactive}"
        )


def due_events() -> models.QuerySet[WebhookEvent]:
    """Events needing a delivery attempt right now.

    Uses the mie_hook_retry_idx index (delivery_status, next_retry_at).
    A PENDING event with next_retry_at IS NULL has never been attempted.
    """

    now = timezone.now()
    return WebhookEvent.objects.filter(
        delivery_status=WebhookDeliveryStatus.PENDING
    ).filter(
        models.Q(next_retry_at__isnull=True) | models.Q(next_retry_at__lte=now)
    ).select_related("submission", "submission__developer").order_by("created_datetime")


def sign_payload(secret: str, timestamp: str, body: bytes) -> str:
    """HMAC-SHA256 hex digest over '{timestamp}.{body}'."""

    message = timestamp.encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def render_body(event: WebhookEvent) -> bytes:
    """Canonical wire body: exactly the stored payload, stable ordering."""

    envelope = {
        "event_id": str(event.id),
        "type": event.event_type,
        "occurred_at": event.created_datetime.isoformat(),
        "submission": event.payload.get("submission", {}),
    }
    return json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()


def dispatch_due_events(now=None) -> DispatchReport:
    """Run one delivery pass over all due events.

    Single-threaded DB reads/writes bracket a bounded thread pool of pure
    HTTP work, so total cost stays linear in due events with constant
    memory per worker.
    """

    events = list(due_events())
    if not events:
        return DispatchReport()

    deliverable, inactive = _partition_by_account_state(events)

    report = DispatchReport()
    if inactive:
        # Terminal accounts can never receive; fail their events now so
        # they stop accumulating (same treatment as explicit rejection).
        report.skipped_inactive = len(inactive)
        report.failed += WebhookEvent.objects.filter(
            id__in=[event.id for event in inactive]
        ).update(
            delivery_status=WebhookDeliveryStatus.FAILED,
            last_error="developer account rejected",
            next_retry_at=None,
        )

    if deliverable:
        prepared = [(event, *_prepare_request(event)) for event in deliverable]
        outcomes = _send_all(prepared)
        _apply_outcomes(outcomes, report)

    return report


def _partition_by_account_state(events):
    """Split into (deliverable, terminal-inactive) without extra queries.

    select_related already loaded each developer; PENDING rows belonging
    to suspended accounts are left untouched (delivered after revival);
    rejected accounts are terminal, so their events fail immediately.
    """

    deliverable, inactive = [], []
    for event in events:
        status = event.submission.developer.status
        if status == DeveloperAccountStatus.APPROVED:
            deliverable.append(event)
        elif status == DeveloperAccountStatus.REJECTED:
            inactive.append(event)
        # SUSPENDED: intentionally neither - frozen, not failed.
    return deliverable, inactive


def _prepare_request(event: WebhookEvent):
    """Compute everything a worker thread needs before touching network."""

    developer = event.submission.developer
    body = render_body(event)
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-MIE-Timestamp": timestamp,
        "X-MIE-Signature": sign_payload(developer.signing_secret, timestamp, body),
    }
    return developer.webhook_url, body, headers


def _send_all(prepared):
    """POST concurrently; return {event_id: (ok, status_code, error)}.

    Threads do no ORM access whatsoever - inputs are plain tuples, so no
    connection sharing, no per-thread cleanup, nothing to leak.
    """

    outcomes = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_post_once, url, body, headers): event.id
            for event, url, body, headers in prepared
        }
        for future in as_completed(futures):
            outcomes[futures[future]] = future.result()
    return outcomes


def _post_once(url: str, body: bytes, headers: dict):
    try:
        response = httpx.post(
            url,
            content=body,
            headers=headers,
            timeout=httpx.Timeout(CONNECT_TIMEOUT_SECONDS, read=READ_TIMEOUT_SECONDS),
        )
        ok = 200 <= response.status_code < 300
        detail = "" if ok else f"HTTP {response.status_code}"
        return ok, response.status_code if not ok else None, detail
    except httpx.HTTPError as exc:
        return False, None, str(exc)[:500]


def _next_retry(attempts: int):
    from datetime import timedelta

    delay = RETRY_DELAYS_SECONDS[min(attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)]
    return timezone.now() + timedelta(seconds=delay)


def _apply_outcomes(outcomes, report: DispatchReport) -> DispatchReport:
    """Fold HTTP results into `report`; one pass of bulk updates."""

    if not outcomes:
        return report

    to_update = []
    for event in WebhookEvent.objects.filter(id__in=list(outcomes)):
        ok, status_code, detail = outcomes[event.id]
        event.attempts += 1
        event.last_response_code = status_code
        event.last_error = detail

        if ok:
            event.delivery_status = WebhookDeliveryStatus.DELIVERED
            event.delivered_at = timezone.now()
            event.next_retry_at = None
            report.delivered += 1
        elif event.attempts >= MAX_ATTEMPTS:
            event.delivery_status = WebhookDeliveryStatus.FAILED
            event.next_retry_at = None
            report.failed += 1
        else:
            event.next_retry_at = _next_retry(event.attempts)
            report.retried += 1
        to_update.append(event)

    WebhookEvent.objects.bulk_update(
        to_update,
        fields=[
            "delivery_status",
            "attempts",
            "last_response_code",
            "last_error",
            "next_retry_at",
            "delivered_at",
            "updated_datetime",
        ],
        batch_size=DELIVERY_BATCH_SIZE,
    )
    return report


def drop_events_for_rejected_account(account) -> int:
    """Terminal-fail any pending events when an account hits REJECTED.

    Called from developer_service.reject_developer so the queue cannot
    grow unbounded behind an account that can never receive again.
    """

    updated = WebhookEvent.objects.filter(
        submission__developer=account,
        delivery_status=WebhookDeliveryStatus.PENDING,
    ).update(
        delivery_status=WebhookDeliveryStatus.FAILED,
        last_error="developer account rejected",
        next_retry_at=None,
    )
    return updated
