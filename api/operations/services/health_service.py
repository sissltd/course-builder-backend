"""System Health aggregation.

Uptime and latency are computed from ServiceHealthSample rows over a
window rather than stored, so the figures cannot silently go stale. A
service with no samples in the window reports null - "we do not know" -
instead of 100%, which would be the most dangerous possible default on a
health dashboard.
"""

from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.utils import timezone

from api.operations.enums import ServiceStatus
from api.operations.models import Service, ServiceHealthSample

DEFAULT_WINDOW_DAYS = 30


def get_system_health(*, window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """Per-service uptime/latency plus the summary tiles above the table."""

    since = timezone.now() - timedelta(days=window_days)

    rows = (
        Service.objects.filter(is_active=True)
        .annotate(
            sample_count=Count("samples", filter=Q(samples__checked_at__gte=since)),
            operational_count=Count(
                "samples",
                filter=Q(
                    samples__checked_at__gte=since,
                    samples__status=ServiceStatus.OPERATIONAL,
                ),
            ),
            avg_latency=Avg(
                "samples__latency_ms", filter=Q(samples__checked_at__gte=since)
            ),
        )
        .order_by("display_order", "name")
    )

    latest_status = _latest_status_by_service(since)

    services = []
    for row in rows:
        services.append(
            {
                "id": str(row.id),
                "name": row.name,
                "priority": row.priority,
                "status": latest_status.get(row.id),
                "uptime_percent": _uptime(row.operational_count, row.sample_count),
                "avg_latency_ms": (
                    round(row.avg_latency) if row.avg_latency is not None else None
                ),
                "sample_count": row.sample_count,
            }
        )

    recovery = _recovery_seconds(since)
    for row in services:
        row["last_recovery_seconds"] = recovery.get(row["id"])

    measured = [s for s in services if s["uptime_percent"] is not None]
    recoveries = [s["last_recovery_seconds"] for s in services
                  if s["last_recovery_seconds"] is not None]
    latencies = [s["avg_latency_ms"] for s in services if s["avg_latency_ms"] is not None]

    return {
        "window_days": window_days,
        "overall_uptime_percent": (
            round(sum(s["uptime_percent"] for s in measured) / len(measured), 2)
            if measured
            else None
        ),
        "avg_api_latency_ms": (
            round(sum(latencies) / len(latencies)) if latencies else None
        ),
        "avg_recovery_seconds": (
            round(sum(recoveries) / len(recoveries)) if recoveries else None
        ),
        "degraded_count": sum(
            1 for s in services if s["status"] == ServiceStatus.DEGRADED
        ),
        "down_count": sum(1 for s in services if s["status"] == ServiceStatus.DOWN),
        "services": services,
    }


def _uptime(operational: int, total: int):
    """Share of samples that were operational, or None if never sampled."""

    if not total:
        return None
    return round((operational / total) * 100, 2)


def _latest_status_by_service(since) -> dict:
    """Most recent status per service, in one pass over the window.

    Sorting in Python over an already-indexed window beats a correlated
    subquery per service, and the window is small by construction.
    """

    latest = {}
    for sample in (
        ServiceHealthSample.objects.filter(checked_at__gte=since)
        .order_by("service_id", "-checked_at")
        .only("service_id", "status", "checked_at")
    ):
        latest.setdefault(sample.service_id, sample.status)
    return latest


def _recovery_seconds(since) -> dict:
    """Most recent time-to-recovery per service, in seconds.

    Recovery is measured from the first sample of a failure run - the
    moment the service stopped being operational - to the first
    operational sample that follows it. Measuring from the *first* failure
    rather than the last means a service that flaps for an hour reports an
    hour, not the few seconds of its final blip, which is the number an
    operator actually cares about.

    Only closed failure runs count. A service still down has not recovered
    and reports None rather than a partial figure that would shrink the
    average and imply things are better than they are.
    """

    recovery = {}
    current_failure_start = {}

    for sample in (
        ServiceHealthSample.objects.filter(checked_at__gte=since)
        .order_by("service_id", "checked_at")
        .only("service_id", "status", "checked_at")
    ):
        service_id = str(sample.service_id)

        if sample.status == ServiceStatus.OPERATIONAL:
            started = current_failure_start.pop(service_id, None)
            if started is not None:
                # Latest recovery wins - the dashboard shows the most
                # recent, not the worst ever.
                recovery[service_id] = int(
                    (sample.checked_at - started).total_seconds()
                )
        elif service_id not in current_failure_start:
            current_failure_start[service_id] = sample.checked_at

    # A service whose failure run never closed is still down.
    for service_id in current_failure_start:
        recovery.pop(service_id, None)

    return recovery
