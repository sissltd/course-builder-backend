import logging

from celery import shared_task

from api.courses.ai.providers import AIProviderError

logger = logging.getLogger(__name__)

RUN_SOFT_TIME_LIMIT_SECONDS = 6 * 60 * 60
RUN_HARD_TIME_LIMIT_SECONDS = 6 * 60 * 60 + 5 * 60
"""Rendering is real time divided by a few per lesson, so a long course
takes hours. A run cut off here is retried, and resumes from what it
already stored."""


@shared_task(
    name="api.production.tasks.run_production",
    soft_time_limit=RUN_SOFT_TIME_LIMIT_SECONDS,
    time_limit=RUN_HARD_TIME_LIMIT_SECONDS,
    acks_late=True,
)
def run_production(run_id: str):
    """Execute one production run. Provider failures marked retryable, a
    storage hiccup, a time limit or a script edited mid-run send the run
    back to the queue (the beat sweep re-dispatches it); anything else fails
    it with an alert."""

    from celery.exceptions import SoftTimeLimitExceeded

    from api.production.services import production_service
    from shared.services.storage_service import StorageError

    try:
        production_service.execute_run(run_id=run_id)
    except AIProviderError as exc:
        logger.warning("production run %s provider error: %s", run_id, exc)
        production_service.fail_run(run_id=run_id, error=str(exc.detail), will_retry=exc.retryable)
    except (StorageError, SoftTimeLimitExceeded, production_service.ScriptChangedDuringRun) as exc:
        logger.warning("production run %s will retry: %s", run_id, exc)
        production_service.fail_run(run_id=run_id, error=str(exc) or type(exc).__name__, will_retry=True)
    except Exception as exc:  # noqa: BLE001 - every failure must land on the run
        logger.exception("production run %s failed", run_id)
        production_service.fail_run(run_id=run_id, error=str(exc), will_retry=False)


@shared_task(name="api.production.tasks.dispatch_production_runs")
def dispatch_production_runs():
    """Beat: requeue runs whose worker went silent and dispatch queued ones."""

    from api.production.services import production_service

    sent = production_service.dispatch_due_runs()
    if sent:
        logger.info("dispatched %s production run(s)", sent)
