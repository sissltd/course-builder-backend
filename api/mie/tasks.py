import logging

from celery import shared_task

from api.mie.services.idempotency_service import purge_expired
from api.mie.services.webhook_dispatcher import dispatch_due_events

logger = logging.getLogger(__name__)


@shared_task
def dispatch_due_webhooks_task():
    """Deliver all due MIE webhook events in one O(N) pass.

    Scheduled every minute via CELERY_BEAT_SCHEDULE. Safe to overlap with
    an in-flight pass: each run only claims events whose next_retry_at has
    elapsed, and outcomes are recorded per event, so the worst case is a
    redundant empty SELECT.
    """

    report = dispatch_due_events()
    if report.delivered or report.retried or report.failed:
        logger.info("mie webhook dispatch: %s", report)
    return str(report)


@shared_task
def purge_expired_idempotency_keys_task():
    """Delete Idempotency-Key rows past their 24-hour lifetime.

    Scheduled hourly via CELERY_BEAT_SCHEDULE. Only housekeeping: an expired
    key is already treated as new when it is sent again.
    """

    deleted = purge_expired()
    if deleted:
        logger.info("mie idempotency purge: %s keys deleted", deleted)
    return deleted
