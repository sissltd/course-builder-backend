import logging

from celery import shared_task

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
