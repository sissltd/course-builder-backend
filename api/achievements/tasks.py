import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def evaluate_creator_badges_task(creator_id: str, criterion: str) -> int:
    """Check one creator for newly earned badges after a course event.

    Failures are logged and swallowed: the course transition that queued this
    has already committed, and a badge problem must never surface as an error
    on it. Returns how many badges were awarded.
    """

    from api.achievements.services import award_service

    try:
        return len(
            award_service.evaluate_creator(creator_id=creator_id, criterion=criterion)
        )
    except Exception:  # noqa: BLE001 - see docstring: never fail the course event
        logger.exception(
            "badge evaluation failed for creator %s on %s", creator_id, criterion
        )
        return 0
