"""The seam between the review pipeline and the production engine.

The production engine prepares a course's final content once the Approver has
priced it, and pushes it to the platforms the Approver chose. It is not built
yet: `finalize` is the one call the pipeline makes into it, kept as a no-op so
the Approver's publish already goes through the place the engine will plug
into. Replacing the body is the whole integration.
"""

import logging

from api.courses.models import Course
from api.users.models import User

logger = logging.getLogger(__name__)


def finalize(*, course: Course, actor: User) -> None:
    """Hand an approved, priced course to final production.

    A no-op until the production engine exists. Called inside
    course_service.publish_course, after the course is marked published.
    """

    logger.info(
        "Final production requested for course %s by %s; the production "
        "engine is not built yet, so nothing was produced.",
        course.id,
        actor.id,
    )
