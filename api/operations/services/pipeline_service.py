"""APE Pipeline aggregation.

Stage counts are counts of PipelineJob rows, so the funnel reflects real
work rather than a projection. Every stage appears even at zero, so the
funnel keeps a stable shape as volume moves through it.
"""

from django.db.models import Avg, Count, F, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from api.operations.enums import (
    STAGE_ORDER,
    PipelineJobStatus,
    PipelineStage,
)
from api.operations.models import PipelineJob, Provider

ACTIVE_STATUSES = (PipelineJobStatus.QUEUED, PipelineJobStatus.RUNNING)
FAILING_STATUSES = (PipelineJobStatus.FAILED, PipelineJobStatus.RETRYING)


def get_pipeline_overview() -> dict:
    """Tiles, per-stage funnel, and provider load for the pipeline screen."""

    today = timezone.localdate()
    stage_labels = dict(PipelineStage.choices)

    per_stage = {
        row["stage"]: row
        for row in (
            PipelineJob.objects.values("stage")
            .annotate(
                total=Count("id"),
                active=Count("id", filter=Q(status__in=ACTIVE_STATUSES)),
                failed=Count("id", filter=Q(status__in=FAILING_STATUSES)),
                completed=Count(
                    "id", filter=Q(status=PipelineJobStatus.COMPLETED)
                ),
            )
        )
    }

    stages = [
        {
            "stage": stage,
            "label": stage_labels[stage],
            "total": per_stage.get(stage, {}).get("total", 0),
            "active": per_stage.get(stage, {}).get("active", 0),
            "completed": per_stage.get(stage, {}).get("completed", 0),
            "failed": per_stage.get(stage, {}).get("failed", 0),
        }
        for stage in STAGE_ORDER
    ]

    return {
        "active_jobs": PipelineJob.objects.filter(status__in=ACTIVE_STATUSES).count(),
        "queue_depth": PipelineJob.objects.filter(
            status=PipelineJobStatus.QUEUED
        ).count(),
        "completed_today": PipelineJob.objects.filter(
            status=PipelineJobStatus.COMPLETED
        )
        .annotate(day=TruncDate("finished_at"))
        .filter(day=today)
        .count(),
        "failed_or_retrying": PipelineJob.objects.filter(
            status__in=FAILING_STATUSES
        ).count(),
        "avg_pipeline_seconds": _avg_pipeline_seconds(),
        "stages": stages,
        "providers": _provider_rows(),
    }


def _avg_pipeline_seconds():
    """Mean wall-clock seconds for completed jobs, or None if none exist."""

    result = (
        PipelineJob.objects.filter(
            status=PipelineJobStatus.COMPLETED,
            started_at__isnull=False,
            finished_at__isnull=False,
        )
        .annotate(duration=F("finished_at") - F("started_at"))
        .aggregate(mean=Avg("duration"))["mean"]
    )
    return round(result.total_seconds()) if result is not None else None


def _provider_rows() -> list:
    """Last-known load/queue per active provider, staleness included."""

    return [
        {
            "id": str(provider.id),
            "name": provider.name,
            "kind": provider.kind,
            "load_percent": provider.current_load_percent,
            "queue_depth": provider.current_queue_depth,
            "readings_updated_at": (
                provider.readings_updated_at.isoformat()
                if provider.readings_updated_at
                else None
            ),
        }
        for provider in Provider.objects.filter(is_active=True)
    ]


# ── Write path ───────────────────────────────────────────────────────
# The production engine records work through these rather than touching
# models directly, so status transitions, timestamps and retry counting
# happen in one place and the dashboard stays consistent.


def record_job(*, course, stage, provider=None):
    """Queue one unit of production work."""

    return PipelineJob.objects.create(
        course=course,
        stage=stage,
        status=PipelineJobStatus.QUEUED,
        provider=provider,
    )


def start_job(*, job):
    """Mark a queued job as running, stamping the clock the funnel uses."""

    job.status = PipelineJobStatus.RUNNING
    job.started_at = timezone.now()
    job.attempts += 1
    job.save(update_fields=["status", "started_at", "attempts", "updated_datetime"])
    return job


def complete_job(*, job):
    """Mark a job finished. `finished_at` drives completed-today and the
    average pipeline duration, so it is always stamped here."""

    job.status = PipelineJobStatus.COMPLETED
    job.finished_at = timezone.now()
    job.last_error = ""
    job.save(
        update_fields=["status", "finished_at", "last_error", "updated_datetime"]
    )
    return job


def fail_job(*, job, error: str, will_retry: bool = False):
    """Record a failure.

    `will_retry` distinguishes a job that is coming back from one that is
    finished with - both show in the Failed/Retry tile, but only the
    latter is terminal, and conflating them would hide a stuck pipeline.
    """

    job.status = (
        PipelineJobStatus.RETRYING if will_retry else PipelineJobStatus.FAILED
    )
    job.last_error = error[:500]
    if not will_retry:
        job.finished_at = timezone.now()
    job.save(
        update_fields=[
            "status",
            "last_error",
            "finished_at",
            "updated_datetime",
        ]
    )
    return job


def update_provider_readings(*, provider, load_percent=None, queue_depth=None):
    """Write a provider's latest load/queue poll.

    `readings_updated_at` is stamped here and nowhere else, so the
    dashboard's staleness indicator can always be trusted.
    """

    provider.current_load_percent = load_percent
    provider.current_queue_depth = queue_depth
    provider.readings_updated_at = timezone.now()
    provider.save(
        update_fields=[
            "current_load_percent",
            "current_queue_depth",
            "readings_updated_at",
            "updated_datetime",
        ]
    )
    return provider
