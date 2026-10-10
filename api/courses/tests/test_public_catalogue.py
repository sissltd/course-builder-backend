"""The public course catalogue: anyone may list and open courses that are
published and live on at least one channel; nothing else exists to them."""

from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from api.courses.enums import CourseStatus, DifficultyLevel, DistributionChannel, DistributionStatus, VideoProvider
from api.courses.models import Course, CourseDistribution
from api.courses.tests.factories import build_compliant_course
from api.users.enums import UserRole
from shared.services.storage_service import StorageService

LIST = "/api/v1/catalogue/courses/"


@pytest.fixture
def creator(make_user):
    return make_user(role=UserRole.COURSE_CREATOR)


@pytest.fixture(autouse=True)
def signed_links():
    with mock.patch.object(StorageService, "generate_presigned_get", lambda value, expires_in=600: f"https://signed.test/{value}?expires={expires_in}"):
        yield


def _course(creator, *, slug, status=CourseStatus.PUBLISHED, live=True, **fields) -> Course:
    course = build_compliant_course(creator=creator, module_count=2, lessons_per_module=2)
    values = {"status": status, "slug": slug, "title": slug.replace("-", " ").title(), "duration_estimate_minutes": 80, **fields}
    for name, value in values.items():
        setattr(course, name, value)
    course.save()
    CourseDistribution.objects.create(
        course=course,
        channel=DistributionChannel.SOLUDESK,
        learner_price="25000.00",
        status=DistributionStatus.PUBLISHED if live else DistributionStatus.FAILED,
    )
    return course


@pytest.mark.django_db
def test_anyone_sees_only_published_courses_that_are_live_somewhere(api_client, creator):
    _course(creator, slug="live-course")
    _course(creator, slug="failed-everywhere", live=False)
    _course(creator, slug="still-in-review", status=CourseStatus.SUBMITTED)

    response = api_client.get(LIST)

    assert response.status_code == 200
    assert [row["slug"] for row in response.data["data"]["results"]] == ["live-course"]
    row = response.data["data"]["results"][0]
    assert row["channels"][0]["channel"] == "SOLUDESK" and row["channels"][0]["price"] == "25000.00"
    assert response["Cache-Control"] == "public, max-age=300"


@pytest.mark.django_db
def test_a_signed_in_users_token_is_not_needed_or_checked(api_client, creator):
    _course(creator, slug="live-course")
    api_client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")

    assert api_client.get(LIST).status_code == 200


@pytest.mark.django_db
def test_the_course_page_shows_the_outline_trailer_disclosure_and_json_ld(api_client, creator):
    _course(
        creator,
        slug="engine-made",
        video_provider=VideoProvider.PRODUCTION_ENGINE,
        thumbnail_url="https://bucket.example.com/thumb.png",
        preview_video_url="https://www.youtube.com/watch?v=abc",
    )

    response = api_client.get(f"{LIST}engine-made/")

    assert response.status_code == 200
    data = response.data
    assert [len(module["lessons"]) for module in data["outline"]] == [2, 2]
    assert "script" not in data["outline"][0]["lessons"][0]  # titles only
    assert data["disclosure"]["ai_narration"] is True and "synthetic" in data["disclosure"]["statement"]
    assert data["trailer_url"] == "https://www.youtube.com/watch?v=abc"  # external links pass through
    assert data["json_ld"]["@type"] == "Course" and data["json_ld"]["timeRequired"] == "PT80M"
    assert data["json_ld"]["offers"][0]["price"] == "25000.00"


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ["failed-everywhere", "still-in-review", "no-such-course"])
def test_anything_unlisted_is_a_404(api_client, creator, slug):
    _course(creator, slug="failed-everywhere", live=False)
    _course(creator, slug="still-in-review", status=CourseStatus.SUBMITTED)

    response = api_client.get(f"{LIST}{slug}/")

    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "not_found"


@pytest.mark.django_db
def test_filters_narrow_by_level_category_and_search(api_client, creator):
    beginner = _course(creator, slug="python-basics", difficulty_level=DifficultyLevel.BEGINNER)
    _course(creator, slug="advanced-go", difficulty_level=DifficultyLevel.ADVANCED)

    by_level = api_client.get(f"{LIST}?level=BEGINNER")
    by_category = api_client.get(f"{LIST}?category={beginner.category.slug}")
    by_search = api_client.get(f"{LIST}?search=python")
    bad_level = api_client.get(f"{LIST}?level=EXPERT")
    too_long = api_client.get(f"{LIST}?search={'x' * 101}")

    assert [row["slug"] for row in by_level.data["data"]["results"]] == ["python-basics"]
    assert [row["slug"] for row in by_category.data["data"]["results"]] == ["python-basics"]
    assert [row["slug"] for row in by_search.data["data"]["results"]] == ["python-basics"]
    assert bad_level.status_code == 400 and bad_level.json()["errors"][0]["field_name"] == "level"
    assert too_long.status_code == 400


@pytest.mark.django_db
def test_the_list_costs_the_same_however_many_courses(api_client, creator):
    _course(creator, slug="course-1")
    with CaptureQueriesContext(connection) as one:
        api_client.get(LIST)
    for index in range(2, 6):
        _course(creator, slug=f"course-{index}")
    with CaptureQueriesContext(connection) as five:
        response = api_client.get(LIST)

    assert len(response.data["data"]["results"]) == 5
    assert len(five) == len(one)


@pytest.mark.django_db
def test_the_course_page_costs_the_same_however_big_the_course(api_client, creator):
    _course(creator, slug="small")
    big = build_compliant_course(creator=creator, module_count=4, lessons_per_module=3)
    Course.objects.filter(pk=big.pk).update(status=CourseStatus.PUBLISHED, slug="big")
    CourseDistribution.objects.create(course=big, channel=DistributionChannel.SOLUDESK, learner_price="1.00", status=DistributionStatus.PUBLISHED)

    with CaptureQueriesContext(connection) as small:
        api_client.get(f"{LIST}small/")
    with CaptureQueriesContext(connection) as large:
        response = api_client.get(f"{LIST}big/")

    assert response.status_code == 200
    assert len(large) == len(small)
