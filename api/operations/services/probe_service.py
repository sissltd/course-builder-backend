"""Health probes that write ServiceHealthSample rows.

The System Health screen reads samples; something has to write them, or
every service reports null forever. This is that something.

Only dependencies this process can genuinely reach are probed for real -
the database, the cache, and object storage. Everything else is
registered but unprobed, and reports null rather than a fabricated
"operational", because a made-up green light on a health dashboard is
worse than an honest blank.
"""

import logging
import time

from django.core.cache import cache
from django.db import connection
from django.utils import timezone

from api.operations.enums import ServiceStatus
from api.operations.models import Service, ServiceHealthSample

logger = logging.getLogger(__name__)

DEGRADED_LATENCY_MS = 500
"""Above this a probe that succeeded is still reported as degraded."""

#: Service name -> callable returning latency in ms, or raising on failure.
#: Names match the seeded registry exactly; a service with no probe here is
#: simply not sampled.
PROBES = {}


def probe(name):
    """Register a probe callable against a seeded service name."""

    def register(func):
        PROBES[name] = func
        return func

    return register


@probe("PostgreSQL")
def _probe_postgres() -> int:
    started = time.monotonic()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return _elapsed_ms(started)


@probe("Redis Cache")
def _probe_cache() -> int:
    started = time.monotonic()
    cache.set("operations:healthprobe", "1", 10)
    if cache.get("operations:healthprobe") != "1":
        raise RuntimeError("cache round-trip returned the wrong value")
    return _elapsed_ms(started)


STORAGE_PROBE_TIMEOUT_SECONDS = 3
"""Bounded so an unreachable endpoint cannot stall the whole sweep.

boto3 otherwise retries with a long default timeout, which would make one
dead dependency delay every other service's sample.
"""


@probe("S3/CDN")
def _probe_storage() -> int:
    """Reachability of the configured bucket, without uploading anything.

    head_bucket is the cheapest call that proves both credentials and the
    bucket - it transfers no object data.
    """

    import boto3
    from botocore.config import Config

    from shared.constants.digital_ocean import (
        DIGITAL_OCEAN_ACCESS_KEY,
        DIGITAL_OCEAN_BUCKET,
        DIGITAL_OCEAN_ENDPOINT,
        DIGITAL_OCEAN_REGION,
        DIGITAL_OCEAN_SECRET_KEY,
    )

    client = boto3.client(
        "s3",
        region_name=DIGITAL_OCEAN_REGION,
        endpoint_url=DIGITAL_OCEAN_ENDPOINT,
        aws_access_key_id=DIGITAL_OCEAN_ACCESS_KEY,
        aws_secret_access_key=DIGITAL_OCEAN_SECRET_KEY,
        config=Config(
            signature_version="s3v4",
            connect_timeout=STORAGE_PROBE_TIMEOUT_SECONDS,
            read_timeout=STORAGE_PROBE_TIMEOUT_SECONDS,
            retries={"max_attempts": 1},
        ),
    )

    started = time.monotonic()
    client.head_bucket(Bucket=DIGITAL_OCEAN_BUCKET)
    return _elapsed_ms(started)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def run_probes() -> dict:
    """Probe every registered service and record one sample each.

    Returns a small report so the caller (management command or beat
    task) can log what happened. A probe raising is recorded as DOWN
    rather than propagating - one broken dependency must not stop the
    others being measured.
    """

    checked_at = timezone.now()
    services = {
        service.name: service
        for service in Service.objects.filter(is_active=True, name__in=PROBES)
    }

    samples, report = [], {"operational": 0, "degraded": 0, "down": 0, "skipped": 0}

    for name, probe_callable in PROBES.items():
        service = services.get(name)
        if service is None:
            report["skipped"] += 1
            continue

        try:
            latency = probe_callable()
            status = (
                ServiceStatus.DEGRADED
                if latency > DEGRADED_LATENCY_MS
                else ServiceStatus.OPERATIONAL
            )
        except Exception as exc:  # noqa: BLE001 - a failed probe is a result
            logger.warning("health probe failed for %s: %s", name, exc)
            latency, status = None, ServiceStatus.DOWN

        report[status.lower()] += 1
        samples.append(
            ServiceHealthSample(
                service=service,
                status=status,
                latency_ms=latency,
                checked_at=checked_at,
            )
        )

    if samples:
        ServiceHealthSample.objects.bulk_create(samples)
    return report
