from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.courses.enums import DifficultyLevel
from api.courses.serializers.public_catalogue_serializer import (
    PublicCourseDetailSerializer,
    PublicCourseSerializer,
)
from api.courses.services import catalogue_service
from includes.helpers.pagination import PageNumberAPIPagination
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

TAG = "Public — Course Catalogue"

CACHE_SECONDS = 300
"""Shorter than the signed media links inside (catalogue_service), so a
cached response never carries an expired link."""

DISCLOSURE_EXAMPLE = {
    "ai_narration": True,
    "ai_generated_content": False,
    "statement": "This course is narrated by an AI-generated (synthetic) voice, and its visuals were produced automatically from the approved course script.",
}
CHANNEL_EXAMPLE = {
    "channel": "SOLUDESK",
    "channel_label": "SoluDesk",
    "price": "25000.00",
    "promotional_price": None,
    "pricing_model": "ONE_TIME",
    "external_course_id": "sd-10432",
    "published_at": "2026-10-12T10:04:00Z",
}
COURSE_EXAMPLE = {
    "slug": "intro-to-python",
    "title": "Intro to Python",
    "description": "Learn Python from scratch...",
    "category": "Data Science & Analytics",
    "category_slug": "data-science-analytics",
    "topic": "Python for Data Analysis",
    "level": "BEGINNER",
    "duration_minutes": 120,
    "thumbnail_url": "https://bucket.example.com/production/...png?X-Amz-Signature=...",
    "disclosure": DISCLOSURE_EXAMPLE,
    "channels": [CHANNEL_EXAMPLE],
    "published_at": "2026-10-12T10:00:00Z",
}


def _cached(response: Response) -> Response:
    response["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return response


class PublicCourseListView(APIView):
    """The public catalogue."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "catalogue"
    pagination_class = PageNumberAPIPagination

    @extend_schema(
        operation_id="public_catalogue_courses_list",
        summary="List catalogue courses",
        description=(
            "Every course that is published and live on at least one channel, "
            "newest first, with its price on each channel and how it was made.\n\n"
            "Called by the public catalogue page and any partner site.\n\n"
            "**Auth:** None; throttled per client IP.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Courses still in review, or published but live "
            "nowhere, never appear. `thumbnail_url` is a signed link that lasts "
            "an hour; responses may be cached for five minutes. Rows are "
            "paginated under `data.results`."
        ),
        tags=[TAG],
        auth=[],
        parameters=[
            OpenApiParameter("category", str, required=False, description="Category slug."),
            OpenApiParameter("topic", str, required=False, description="Topic slug."),
            OpenApiParameter("level", str, enum=DifficultyLevel.values, required=False, description="Difficulty level."),
            OpenApiParameter("search", str, required=False, description="Words in the title or description (at most 100 characters)."),
            OpenApiParameter("page", int, required=False, description="Page number."),
            OpenApiParameter("size", int, required=False, description="Rows per page."),
        ],
        responses={
            200: OpenApiResponse(
                response=PublicCourseSerializer(many=True),
                description="A page of catalogue courses.",
                examples=[OpenApiExample("Courses", value=[COURSE_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        params = request.query_params
        queryset = catalogue_service.catalogue_queryset(
            category=params.get("category") or None,
            topic=params.get("topic") or None,
            level=params.get("level") or None,
            search=params.get("search") or None,
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, self)
        return _cached(paginator.get_paginated_response(PublicCourseSerializer(page, many=True).data))


class PublicCourseDetailView(APIView):
    """One catalogue course."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "catalogue"

    @extend_schema(
        operation_id="public_catalogue_courses_retrieve",
        summary="Get a catalogue course",
        description=(
            "The course page: everything in the list row, plus learning "
            "objectives, the trailer, the outline (module and lesson titles "
            "only) and schema.org JSON-LD for the page head.\n\n"
            "Called when a visitor opens a course page.\n\n"
            "**Auth:** None; throttled per client IP.\n\n"
            "**Prerequisites:** The course is published and live on at least "
            "one channel.\n\n"
            "**Important:** Any other slug is a 404, including a course in "
            "review. Lesson content and videos are not public; `trailer_url` "
            "and `thumbnail_url` are signed links that last an hour."
        ),
        tags=[TAG],
        auth=[],
        responses={
            200: OpenApiResponse(
                response=PublicCourseDetailSerializer,
                description="The course page.",
                examples=[
                    OpenApiExample(
                        "Course",
                        value={
                            **COURSE_EXAMPLE,
                            "learning_objectives": ["Write a Python script", "Use lists and dictionaries", "Read a CSV file"],
                            "trailer_url": "https://bucket.example.com/production/...mp4?X-Amz-Signature=...",
                            "outline": [{"title": "Getting started", "order": 1, "lessons": [{"title": "Installing Python", "order": 1, "duration_minutes": 8, "content_type": "VIDEO"}]}],
                            "json_ld": {"@context": "https://schema.org", "@type": "Course", "name": "Intro to Python"},
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, slug):
        course = catalogue_service.get_course(slug=slug)
        return _cached(Response(PublicCourseDetailSerializer(course).data))
