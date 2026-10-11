"""The seam between the review pipeline and the production engine.

The Approver's publish hands the course to final production: with the
engine switched on, a PACKAGE run builds the course's final package and
delivers it to every channel the Approver chose (including SoluDesk, which
is then marked published only once the push lands). With it off, nothing
is produced and SoluDesk is marked published at the click, as before.
"""

from api.courses.models import Course
from api.platform.services import platform_settings_service
from api.production.enums import RunKind
from api.production.services import production_service
from api.users.models import User


def is_enabled() -> bool:
    return platform_settings_service.get_settings().production_enabled


def finalize(*, course: Course, actor: User) -> None:
    """Hand an approved, priced, just-published course to final production.

    Called inside course_service.publish_course, after the course is marked
    published. A no-op while the engine is switched off.
    """

    if is_enabled():
        production_service.request_production(course=course, actor=actor, kind=RunKind.PACKAGE)
