import logging

from celery import shared_task

from api.operations.services.probe_service import run_probes

logger = logging.getLogger(__name__)


@shared_task
def probe_service_health_task():
    """Record a health sample per probeable service.

    Scheduled every five minutes via CELERY_BEAT_SCHEDULE. Cheap and
    idempotent: each run appends samples, and the System Health screen
    computes uptime from whatever window it is asked for, so a missed run
    lowers confidence (visible as sample_count) rather than corrupting a
    figure.
    """

    report = run_probes()
    logger.info("service health probes: %s", report)
    return report
