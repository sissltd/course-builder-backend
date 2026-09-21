import logging

from celery import shared_task

from api.reviews.services.review_sla_service import run_sla_sweep

logger = logging.getLogger(__name__)


@shared_task
def run_sla_sweep_task():
    """Alert admins about overdue reviews and flag stalled courses.

    Scheduled every five minutes via CELERY_BEAT_SCHEDULE. The thresholds
    behind it are measured in hours, so a missed run costs a few minutes of
    delay and nothing else: each course carries the marker that keeps the
    next pass from alerting on it twice, which also makes overlapping runs
    safe.
    """

    report = run_sla_sweep()
    logger.info("review sla sweep: %s", report)
    return report
