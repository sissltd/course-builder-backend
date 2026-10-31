"""Send recorded studio.receipt.v1 callbacks to Studio's private receiver.

The same record-then-sweep shape as webhook_dispatcher: delivery code only
writes StudioDeliveryReceipt rows (studio_receipt_service.record); this
module is the sole sender, run every minute by send_studio_receipts_task.
One indexed query loads the due rows, POSTs run on a small thread pool with
no database work inside threads, and outcomes are written back in one bulk
update.

Each attempt signs the unchanged receipt body afresh:

    X-Receipt-Key-Id  the registered, non-secret key id
    X-MIE-Timestamp   unix seconds at send time
    X-MIE-Signature   hex HMAC-SHA256(secret, f"{timestamp}.{raw_body}")

TLS is always verified against the private CA in STUDIO_RECEIPT_CA_FILE,
with an optional client certificate for mutual TLS. Nothing is sent until
the receiver URL, key id, secret and CA file are all configured; until then
receipts wait as PENDING.

Outcomes follow the receiver's contract: 2xx is delivered; 429, 5xx and
transport errors retry on the webhook schedule (honouring a longer
Retry-After); any other answer, or running out of attempts, holds the
receipt for manual reconciliation and logs an error as the alert.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import timedelta

import httpx
from django.conf import settings
from django.db import models
from django.utils import timezone

from api.mie.enums import StudioReceiptStatus
from api.mie.models import StudioDeliveryReceipt
from api.mie.services.webhook_dispatcher import (
    CONNECT_TIMEOUT_SECONDS,
    MAX_ATTEMPTS,
    MAX_WORKERS,
    READ_TIMEOUT_SECONDS,
    RETRY_DELAYS_SECONDS,
    sign_payload,
)

logger = logging.getLogger(__name__)

DELIVERY_BATCH_SIZE = 200
"""Due receipts sent per pass; the rest wait for the next minute."""

RETRYABLE_STATUSES = frozenset({429})
"""Besides every 5xx: answers the receiver asks us to retry."""


@dataclass
class SendReport:
    delivered: int = 0
    retried: int = 0
    held: int = 0

    def __str__(self):
        return f"delivered={self.delivered} retried={self.retried} held={self.held}"


@dataclass(frozen=True)
class Outcome:
    ok: bool
    status_code: int | None
    error: str
    retry_after: int | None
    response: dict


def is_configured() -> bool:
    return all(
        (
            settings.STUDIO_RECEIPT_URL,
            settings.STUDIO_RECEIPT_KEY_ID,
            settings.STUDIO_RECEIPT_SECRET,
            settings.STUDIO_RECEIPT_CA_FILE,
        )
    )


def due_receipts() -> models.QuerySet[StudioDeliveryReceipt]:
    """PENDING receipts whose next attempt is due (mie_studio_rcpt_retry_idx)."""

    return (
        StudioDeliveryReceipt.objects.filter(status=StudioReceiptStatus.PENDING)
        .filter(
            models.Q(next_retry_at__isnull=True)
            | models.Q(next_retry_at__lte=timezone.now())
        )
        .order_by("created_datetime")
    )


def render_body(receipt: StudioDeliveryReceipt) -> bytes:
    """The receipt as sent: the stored payload, byte-identical on every retry."""

    return json.dumps(
        receipt.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def send_due_receipts() -> SendReport:
    """Run one sending pass over the due receipts."""

    if not is_configured():
        return SendReport()
    receipts = list(due_receipts()[:DELIVERY_BATCH_SIZE])
    if not receipts:
        return SendReport()
    bodies = {receipt.id: render_body(receipt) for receipt in receipts}
    outcomes = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_post_once, body): receipt_id
            for receipt_id, body in bodies.items()
        }
        for future in as_completed(futures):
            outcomes[futures[future]] = future.result()
    return _apply_outcomes(receipts, outcomes)


def _headers(body: bytes) -> dict:
    timestamp = str(int(time.time()))
    return {
        "Content-Type": "application/json",
        "X-Receipt-Key-Id": settings.STUDIO_RECEIPT_KEY_ID,
        "X-MIE-Timestamp": timestamp,
        "X-MIE-Signature": sign_payload(
            settings.STUDIO_RECEIPT_SECRET, timestamp, body
        ),
    }


def _client_cert():
    if settings.STUDIO_RECEIPT_CLIENT_CERT and settings.STUDIO_RECEIPT_CLIENT_KEY:
        return (settings.STUDIO_RECEIPT_CLIENT_CERT, settings.STUDIO_RECEIPT_CLIENT_KEY)
    return None


def _post_once(body: bytes) -> Outcome:
    try:
        with httpx.Client(
            verify=settings.STUDIO_RECEIPT_CA_FILE,
            cert=_client_cert(),
            timeout=httpx.Timeout(CONNECT_TIMEOUT_SECONDS, read=READ_TIMEOUT_SECONDS),
        ) as client:
            response = client.post(
                settings.STUDIO_RECEIPT_URL, content=body, headers=_headers(body)
            )
    except httpx.HTTPError as exc:
        return Outcome(False, None, str(exc)[:500], None, {})
    ok = 200 <= response.status_code < 300
    return Outcome(
        ok=ok,
        status_code=response.status_code,
        error="" if ok else f"HTTP {response.status_code}",
        retry_after=_retry_after(response),
        response=_json(response) if ok else {},
    )


def _retry_after(response) -> int | None:
    value = response.headers.get("Retry-After", "")
    return int(value) if value.isdigit() else None


def _json(response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _retryable(outcome: Outcome) -> bool:
    code = outcome.status_code
    return code is None or code >= 500 or code in RETRYABLE_STATUSES


def _apply_outcomes(receipts, outcomes) -> SendReport:
    report = SendReport()
    now = timezone.now()
    for receipt in receipts:
        outcome = outcomes[receipt.id]
        receipt.attempts += 1
        receipt.updated_datetime = now
        receipt.last_response_code = outcome.status_code
        receipt.last_error = outcome.error
        if outcome.ok:
            receipt.status = StudioReceiptStatus.DELIVERED
            receipt.delivered_at = now
            receipt.next_retry_at = None
            receipt.response = outcome.response
            report.delivered += 1
        elif _retryable(outcome) and receipt.attempts < MAX_ATTEMPTS:
            delay = RETRY_DELAYS_SECONDS[
                min(receipt.attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)
            ]
            receipt.next_retry_at = now + timedelta(
                seconds=max(delay, outcome.retry_after or 0)
            )
            report.retried += 1
        else:
            receipt.status = StudioReceiptStatus.HELD
            receipt.next_retry_at = None
            report.held += 1
            logger.error(
                "studio receipt held for reconciliation: receipt=%s delivery=%s "
                "kind=%s attempts=%s error=%s",
                receipt.id,
                receipt.delivery_id,
                receipt.kind,
                receipt.attempts,
                outcome.error,
            )
    StudioDeliveryReceipt.objects.bulk_update(
        receipts,
        fields=[
            "status",
            "attempts",
            "last_response_code",
            "last_error",
            "next_retry_at",
            "delivered_at",
            "response",
            "updated_datetime",
        ],
    )
    return report


def requeue(queryset) -> int:
    """Send HELD receipts again, unchanged, once the cause is fixed."""

    return queryset.filter(status=StudioReceiptStatus.HELD).update(
        status=StudioReceiptStatus.PENDING,
        attempts=0,
        next_retry_at=None,
        last_error="",
        updated_datetime=timezone.now(),
    )
