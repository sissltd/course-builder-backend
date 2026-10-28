"""Idempotency-Key support for the developer POSTs that create or replace work.

A developer may send an `Idempotency-Key` header on a submission, a course
push or a video push. The first request with a key claims it and, once it
succeeds, stores its response; a retry with the same key and the same body
gets that response back instead of running again. Without the header nothing
changes.

* A request that fails (4xx or 5xx) releases its key, so the developer can fix
  the body and retry with the same key.
* The same key with a different body, or on another route, is refused
  (IdempotencyKeyReused).
* A retry while the first request is still running is refused
  (IdempotencyRequestInProgress). A claim left behind by a request that never
  finished is taken over after IN_FLIGHT_LEASE.
* Keys are kept for KEY_TTL, then purged; an expired key can be used afresh.

Keys are scoped per developer account: two developers can use the same key.
"""

import hashlib
import json
import string
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import exceptions

from api.mie.exceptions import IdempotencyKeyReused, IdempotencyRequestInProgress
from api.mie.models import DeveloperAccount, IdempotencyKey

KEY_TTL = timedelta(hours=24)
"""How long a key is remembered. A retry later than this runs again."""

IN_FLIGHT_LEASE = timedelta(minutes=5)
"""How long an unfinished claim blocks retries before it counts as abandoned
(the request that made it crashed). Far above any push's run time."""

KEY_MAX_LENGTH = 255
"""Matches IdempotencyKey.key."""

_ALLOWED_KEY_CHARACTERS = frozenset(string.ascii_letters + string.digits + "-_.:")


@dataclass(frozen=True)
class Claim:
    """The outcome of claiming a key: a fresh claim to run the request
    under, or a stored response to replay."""

    record: IdempotencyKey
    replay: bool


def fingerprint(body) -> str:
    """SHA-256 of the body as canonical JSON, so key order and whitespace do
    not make two equal bodies look different."""

    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_key(key: str) -> str:
    """The key as sent, or ValidationError when it is empty, too long, or
    holds characters outside letters, digits and `-_.:`."""

    if not key or len(key) > KEY_MAX_LENGTH or not set(key) <= _ALLOWED_KEY_CHARACTERS:
        raise exceptions.ValidationError(
            {
                "Idempotency-Key": [
                    f"Send 1 to {KEY_MAX_LENGTH} characters: letters, digits, "
                    "'-', '_', '.' or ':'. A UUID works."
                ]
            }
        )
    return key


def claim(
    *, developer: DeveloperAccount, key: str, scope: str, request_hash: str, now=None
) -> Claim:
    """Claim `key` for this request, or find the response to replay.

    Raises IdempotencyKeyReused when the key belongs to a different request
    and IdempotencyRequestInProgress while that request is still running.
    """

    now = now or timezone.now()
    try:
        with transaction.atomic():
            record = IdempotencyKey.objects.create(
                developer=developer, key=key, scope=scope, request_hash=request_hash
            )
        return Claim(record=record, replay=False)
    except IntegrityError:
        pass

    record = IdempotencyKey.objects.get(developer=developer, key=key)
    expired = record.created_datetime < now - KEY_TTL
    if expired:
        return _take_over(
            record=record, scope=scope, request_hash=request_hash, now=now
        )
    if record.scope != scope or record.request_hash != request_hash:
        raise IdempotencyKeyReused()
    if record.completed_at is not None:
        return Claim(record=record, replay=True)
    if record.updated_datetime < now - IN_FLIGHT_LEASE:
        return _take_over(
            record=record, scope=scope, request_hash=request_hash, now=now
        )
    raise IdempotencyRequestInProgress()


def complete(*, record: IdempotencyKey, status_code: int, body) -> None:
    """Store the successful response a retry with this key will receive."""

    record.response_status = status_code
    record.response_body = body
    record.completed_at = timezone.now()
    record.save(
        update_fields=[
            "response_status",
            "response_body",
            "completed_at",
            "updated_datetime",
        ]
    )


def release(*, record: IdempotencyKey) -> None:
    """Drop an unfinished claim after its request failed, so the key can be
    retried. A completed key is never dropped here."""

    IdempotencyKey.objects.filter(pk=record.pk, completed_at__isnull=True).delete()


def purge_expired(now=None) -> int:
    """Delete every key older than KEY_TTL. Returns how many were deleted."""

    cutoff = (now or timezone.now()) - KEY_TTL
    deleted, _ = IdempotencyKey.objects.filter(created_datetime__lt=cutoff).delete()
    return deleted


def _take_over(*, record: IdempotencyKey, scope: str, request_hash: str, now) -> Claim:
    """Restart an expired or abandoned key for this request.

    A conditional update, so of two retries racing for the same stale key
    exactly one wins; the other is told the request is in progress.
    """

    stale = Q(created_datetime__lt=now - KEY_TTL) | Q(
        completed_at__isnull=True, updated_datetime__lt=now - IN_FLIGHT_LEASE
    )
    taken = IdempotencyKey.objects.filter(stale, pk=record.pk).update(
        scope=scope,
        request_hash=request_hash,
        response_status=None,
        response_body=None,
        completed_at=None,
        created_datetime=now,
        updated_datetime=now,
    )
    if not taken:
        raise IdempotencyRequestInProgress()
    record.refresh_from_db()
    return Claim(record=record, replay=False)
