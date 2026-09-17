"""Moving unpublished courses from one course version to another.

A CourseVersion is the label a course publishes under. When a version is
retired, courses still in progress on it can be moved to the current one in
bulk, so they publish under it. Published courses are never touched: their
snapshot records the version they went out under.
"""

from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.services import activity_service
from api.authorization import codenames
from api.authorization.services import permission_service
from api.courses.enums import CourseStatus
from api.courses.models import Course, CourseVersion
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.models import User

#: Statuses of a course that has not been published yet.
MIGRATABLE_STATUSES = (
    CourseStatus.DRAFT,
    CourseStatus.NEEDS_REVISION,
    CourseStatus.SUBMITTED,
    CourseStatus.IN_REVIEW,
    CourseStatus.QA_VERIFICATION,
    CourseStatus.APPROVED,
)


def list_versions(*, actor: User) -> QuerySet[CourseVersion]:
    """Every version, active or not, with how many courses sit on it by status.

    One query: a conditional count per migratable status plus published.
    """

    permission_service.require_permission(
        actor, codenames.COURSES_FORCE_VERSION_MIGRATION
    )
    counts = {
        f"{status.lower()}_count": Count("courses", filter=Q(courses__status=status))
        for status in (*MIGRATABLE_STATUSES, CourseStatus.PUBLISHED)
    }
    return CourseVersion.objects.annotate(
        migratable_count=Count(
            "courses", filter=Q(courses__status__in=MIGRATABLE_STATUSES)
        ),
        **counts,
    ).order_by("label")


def migrate_course_versions(
    *, actor: User, from_version_id, to_version_id, dry_run: bool = False, request=None
) -> dict:
    """Move every unpublished course on `from_version` to `to_version`.

    A fixed number of queries however many courses move: lock and read the
    ids, one UPDATE, one bulk audit insert. `dry_run` reports what would move
    and writes nothing.
    """

    permission_service.require_permission(
        actor, codenames.COURSES_FORCE_VERSION_MIGRATION
    )
    versions = {
        v.id: v
        for v in CourseVersion.objects.filter(id__in=[from_version_id, to_version_id])
    }
    source, target = versions.get(from_version_id), versions.get(to_version_id)
    if source is None or target is None:
        raise exceptions.NotFound("Course version not found.")
    if source.id == target.id:
        raise exceptions.ValidationError(
            {"to_version_id": "Choose a different version to move courses to."}
        )
    if not target.is_active:
        raise exceptions.ValidationError(
            {"to_version_id": "Courses can only be moved to an active version."}
        )

    candidates = Course.objects.filter(version=source, status__in=MIGRATABLE_STATUSES)
    if dry_run:
        by_status = dict(
            candidates.values("status")
            .annotate(total=Count("id"))
            .values_list("status", "total")
        )
        return _result(source, target, by_status, dry_run=True)

    with transaction.atomic():
        courses = list(
            candidates.select_for_update().only("id", "status", "creator_id")
        )
        by_status = {}
        for course in courses:
            by_status[course.status] = by_status.get(course.status, 0) + 1
        Course.objects.filter(id__in=[course.id for course in courses]).update(
            version=target, updated_by=actor, updated_datetime=timezone.now()
        )
        activity_service.bulk_log_activity(
            entries=[
                {
                    "user": actor,
                    "category": UserActivityCategoryEnums.COURSE,
                    "action": UserActivityActionEnums.COURSE_VERSION_MIGRATED,
                    "summary": f"Moved a course from version {source.label} to {target.label}.",
                    "details": {
                        "from_version_id": str(source.id),
                        "to_version_id": str(target.id),
                        "status": course.status,
                    },
                    "target": course,
                }
                for course in courses
            ]
        )
    return _result(source, target, by_status, dry_run=False)


def _result(source, target, by_status: dict, *, dry_run: bool) -> dict:
    return {
        "from_version": source,
        "to_version": target,
        "dry_run": dry_run,
        "courses_moved": sum(by_status.values()),
        "courses_by_status": by_status,
    }
