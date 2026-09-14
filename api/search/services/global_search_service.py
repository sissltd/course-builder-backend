from __future__ import annotations

import uuid

from django.db.models import Q, QuerySet

from api.catalog.models import Category, Topic
from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.users.enums import UserRole
from api.users.models import User

DEFAULT_LIMIT = 5
MAX_LIMIT = 10

ADMIN_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN)
REVIEWER_ROLES = (
    UserRole.CREATOR_REVIEWER,
    UserRole.STAFF_VERIFIER,
    UserRole.STAFF_APPROVER,
    UserRole.QA_REVIEWER,
)
CREATOR_ROLES = (UserRole.COURSE_CREATOR, UserRole.STAFF_WRITER)
REVIEWABLE_STATUSES = (
    CourseStatus.SUBMITTED,
    CourseStatus.IN_REVIEW,
    CourseStatus.QA_VERIFICATION,
    CourseStatus.APPROVED,
    CourseStatus.PUBLISHED,
)


def search(*, actor: User, query: str, limit: int = DEFAULT_LIMIT) -> dict:
    """Search the platform with buckets scoped to the caller's role."""

    normalized_query = " ".join(query.split())
    capped_limit = max(1, min(limit, MAX_LIMIT))
    buckets = {}

    course_results = _search_courses(
        actor=actor,
        query=normalized_query,
        limit=capped_limit,
    )
    if course_results:
        buckets["courses"] = _bucket(course_results)

    category_results = _search_categories(query=normalized_query, limit=capped_limit)
    if category_results:
        buckets["categories"] = _bucket(category_results)

    topic_results = _search_topics(query=normalized_query, limit=capped_limit)
    if topic_results:
        buckets["topics"] = _bucket(topic_results)

    if actor.is_superuser or actor.role in ADMIN_ROLES:
        user_results = _search_users(query=normalized_query, limit=capped_limit)
        if user_results:
            buckets["users"] = _bucket(user_results)

    return {
        "query": normalized_query,
        "limit": capped_limit,
        "total_count": sum(bucket["count"] for bucket in buckets.values()),
        "results": buckets,
    }


def _search_courses(*, actor: User, query: str, limit: int) -> list[dict]:
    queryset = _course_scope(actor=actor)
    if queryset is None:
        return []

    filters = (
        Q(title__icontains=query)
        | Q(description__icontains=query)
        | Q(category__name__icontains=query)
        | Q(topic__name__icontains=query)
        | Q(creator__email__icontains=query)
        | Q(creator__first_name__icontains=query)
        | Q(creator__last_name__icontains=query)
    )
    query_uuid = _uuid_or_none(query)
    if query_uuid is not None:
        filters |= Q(id=query_uuid)

    courses = (
        queryset.filter(filters)
        .select_related("category", "topic", "creator")
        .order_by("-updated_datetime")[:limit]
    )
    return [
        {
            "type": "course",
            "id": str(course.id),
            "title": course.title,
            "subtitle": _course_subtitle(course),
            "status": course.status,
            "api_path": _course_api_path(actor=actor, course=course),
        }
        for course in courses
    ]


def _course_scope(*, actor: User) -> QuerySet[Course] | None:
    if actor.is_superuser or actor.role in ADMIN_ROLES:
        return Course.objects.all()
    if actor.role in REVIEWER_ROLES:
        return Course.objects.filter(status__in=REVIEWABLE_STATUSES)
    if actor.role in CREATOR_ROLES:
        return Course.objects.filter(creator=actor)
    return None


def _search_categories(*, query: str, limit: int) -> list[dict]:
    categories = Category.objects.filter(
        Q(name__icontains=query)
        | Q(slug__icontains=query)
        | Q(description__icontains=query)
    ).order_by("name")[:limit]
    return [
        {
            "type": "category",
            "id": str(category.id),
            "title": category.name,
            "subtitle": category.description,
            "status": category.status,
            "api_path": f"/api/v1/categories/{category.id}/",
        }
        for category in categories
    ]


def _search_topics(*, query: str, limit: int) -> list[dict]:
    topics = (
        Topic.objects.filter(
            Q(name__icontains=query)
            | Q(slug__icontains=query)
            | Q(category__name__icontains=query)
        )
        .select_related("category")
        .order_by("name")[:limit]
    )
    return [
        {
            "type": "topic",
            "id": str(topic.id),
            "title": topic.name,
            "subtitle": topic.category.name,
            "status": topic.status,
            "api_path": f"/api/v1/topics/{topic.id}/",
        }
        for topic in topics
    ]


def _search_users(*, query: str, limit: int) -> list[dict]:
    filters = (
        Q(email__icontains=query)
        | Q(first_name__icontains=query)
        | Q(last_name__icontains=query)
        | Q(role__icontains=query)
    )
    query_uuid = _uuid_or_none(query)
    if query_uuid is not None:
        filters |= Q(id=query_uuid)

    users = User.objects.filter(filters).order_by("-created_datetime")[:limit]
    return [
        {
            "type": "user",
            "id": str(user.id),
            "title": user.get_full_name() or user.email,
            "subtitle": user.email,
            "status": user.status,
            "api_path": f"/api/v1/users/admin/{user.id}/",
        }
        for user in users
    ]


def _bucket(results: list[dict]) -> dict:
    return {"count": len(results), "results": results}


def _course_subtitle(course: Course) -> str:
    creator = course.creator.get_full_name() or course.creator.email
    return f"{course.category.name} - {creator}"


def _course_api_path(*, actor: User, course: Course) -> str:
    if actor.is_superuser or actor.role in ADMIN_ROLES:
        return f"/api/v1/admin/courses/{course.id}/"
    if actor.role in REVIEWER_ROLES:
        return f"/api/v1/review-queue/{course.id}/"
    return f"/api/v1/courses/{course.id}/"


def _uuid_or_none(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
