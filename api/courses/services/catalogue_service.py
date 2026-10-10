"""The public course catalogue: what anyone may see of published courses.

A course is listed once it is published and live on at least one channel.
Anything else, including a published course whose every delivery failed,
is a 404, never a 403, so unlisted courses cannot be discovered.
"""

from django.db.models import Exists, OuterRef, Prefetch, Q
from rest_framework import exceptions

from api.courses.enums import CourseStatus, DifficultyLevel, DistributionStatus
from api.courses.models import Course, CourseDistribution, Lesson, Module
from shared.services.storage_service import StorageService

PUBLIC_MEDIA_SECONDS = 60 * 60
"""Signed links to the thumbnail and trailer last an hour; responses are
cached for less (see the views), so a cached page never holds a dead link."""

SEARCH_MAX_LENGTH = 100


def _live_channels():
    return Prefetch(
        "distribution_channels",
        queryset=CourseDistribution.objects.filter(status=DistributionStatus.PUBLISHED).order_by("channel"),
        to_attr="live_channels",
    )


def _listed():
    live = CourseDistribution.objects.filter(course=OuterRef("pk"), status=DistributionStatus.PUBLISHED)
    return Course.objects.filter(status=CourseStatus.PUBLISHED, slug__isnull=False).filter(Exists(live))


def catalogue_queryset(*, category: str | None = None, topic: str | None = None, level: str | None = None, search: str | None = None):
    """Listed courses, newest publication first, filtered by category or
    topic slug, level and a title search. 400 for an unknown level or an
    over-long search."""

    if level and level not in DifficultyLevel.values:
        raise exceptions.ValidationError({"level": [f"Must be one of {DifficultyLevel.values}."]})
    if search and len(search) > SEARCH_MAX_LENGTH:
        raise exceptions.ValidationError({"search": [f"At most {SEARCH_MAX_LENGTH} characters."]})
    queryset = _listed().select_related("category", "topic").prefetch_related(_live_channels())
    if category:
        queryset = queryset.filter(category__slug=category)
    if topic:
        queryset = queryset.filter(topic__slug=topic)
    if level:
        queryset = queryset.filter(difficulty_level=level)
    if search:
        queryset = queryset.filter(Q(title__icontains=search) | Q(description__icontains=search))
    return queryset.order_by("-published_at", "id")


def get_course(*, slug: str) -> Course:
    """One listed course with its outline. 404 for anything not listed."""

    course = (
        _listed()
        .filter(slug=slug)
        .select_related("category", "topic")
        .prefetch_related(
            _live_channels(),
            Prefetch(
                "modules",
                queryset=Module.objects.order_by("order").prefetch_related(
                    Prefetch("lessons", queryset=Lesson.objects.order_by("order").only("id", "module_id", "title", "order", "duration_minutes", "content_type"))
                ),
            ),
        )
        .first()
    )
    if course is None:
        raise exceptions.NotFound("Course not found.")
    return course


def public_media(value: str) -> str:
    """A link anyone can open: our private files signed for an hour, other
    links as they are."""

    if not value:
        return ""
    if value.startswith(("http://", "https://")) and not value.startswith(StorageService.public_url("")):
        return value
    return StorageService.generate_presigned_get(value, expires_in=PUBLIC_MEDIA_SECONDS) or ""


def json_ld(course: Course) -> dict:
    """schema.org Course markup for the course page. Search engines no
    longer show course rich results (June 2025), but the markup still
    describes the page."""

    data = {
        "@context": "https://schema.org",
        "@type": "Course",
        "name": course.title,
        "description": course.description,
        "inLanguage": "en",
        "educationalLevel": course.get_difficulty_level_display(),
        "provider": {"@type": "Organization", "name": "SoluDesk"},
        "timeRequired": f"PT{course.duration_estimate_minutes or 0}M",
    }
    offers = [
        {"@type": "Offer", "category": channel.get_channel_display(), "price": str(channel.learner_price)}
        for channel in getattr(course, "live_channels", [])
    ]
    if offers:
        data["offers"] = offers
    return data
