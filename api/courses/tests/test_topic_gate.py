"""A creator's course starts from an approved topic when the staged review
flow is on.

A topic exists once it was in the catalogue or an admin approved the
creator's request for it, so requiring one is what turns request-then-approve
into the only way to a new subject. Topic availability (active status) is
checked whatever the switch says.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from api.catalog.enums import CategoryStatus, ReservationStatus
from api.catalog.models import Topic
from api.courses.enums import CourseSourceType
from api.courses.models import AIGenerationJob, Course, CourseImportJob
from api.courses.tests.factories import (
    make_category,
    make_topic,
    make_topic_reservation_request,
)
from api.notification.models import Notification
from api.platform.services import platform_settings_service
from api.users.enums import UserRole

CSV_BYTES = b"module,title,content\nBasics,Install Python,Install Python locally.\n"


@pytest.fixture
def staged_flow(db):
    platform_settings_service.update_settings(staged_review_flow_enabled=True)


@pytest.fixture
def creator(make_user):
    return make_user(role=UserRole.COURSE_CREATOR)


@pytest.fixture
def category(db):
    return make_category()


def _course_body(category, **overrides):
    body = {
        "category": str(category.id),
        "title": "My Course",
        "description": "d" * 20,
        "terms_accepted": True,
    }
    body.update(overrides)
    return body


def _ai_body(category, **overrides):
    body = {
        "title": "Practical Data Analysis",
        "description": "A practical course for new analysts.",
        "category": str(category.id),
        "terms_accepted": True,
        "idempotency_key": "create-123",
    }
    body.update(overrides)
    return body


def _import_body(category, **overrides):
    body = {
        "file_key": "uploads/course-imports/outline.csv",
        "filename": "outline.csv",
        "content_type": "text/csv",
        "size": len(CSV_BYTES),
        "category": str(category.id),
        "title": "Imported CSV Course",
        "description": "Course imported from CSV.",
        "terms_accepted": True,
        "idempotency_key": "import-1",
    }
    body.update(overrides)
    return body


def _topic_error(response) -> dict:
    return next(
        error
        for error in response.json()["errors"]
        if error["field_name"] == "topic"
    )


# --- POST /courses/ -----------------------------------------------------------


@pytest.mark.django_db
def test_on_a_course_without_a_topic_is_refused(api_client, creator, category, staged_flow):
    api_client.force_authenticate(creator)

    response = api_client.post("/api/v1/courses/", _course_body(category), format="json")

    assert response.status_code == 400
    assert "request one and wait" in _topic_error(response)["message"]
    assert not Course.objects.exists()


@pytest.mark.django_db
def test_on_a_course_on_an_available_topic_is_created_and_reserves_it(
    api_client, creator, category, staged_flow
):
    topic = make_topic(category=category)
    api_client.force_authenticate(creator)

    response = api_client.post(
        "/api/v1/courses/",
        _course_body(category, topic=str(topic.id)),
        format="json",
    )

    assert response.status_code == 201
    topic.refresh_from_db()
    assert topic.reserved_by_id == creator.id


@pytest.mark.django_db
def test_on_a_topic_the_creator_already_reserved_by_request_can_be_used(
    api_client, creator, category, staged_flow, make_user
):
    admin = make_user(role=UserRole.ADMIN)
    request = make_topic_reservation_request(requested_by=creator, category=category)
    api_client.force_authenticate(admin)
    approved = api_client.post(f"/api/v1/topic-reservations/{request.id}/approve/")
    assert approved.status_code == 200
    topic = Topic.objects.get(name=request.name)
    api_client.force_authenticate(creator)

    response = api_client.post(
        "/api/v1/courses/",
        _course_body(category, topic=str(topic.id)),
        format="json",
    )

    assert response.status_code == 201


@pytest.mark.django_db
def test_a_topic_reserved_by_someone_else_is_refused(
    api_client, creator, category, staged_flow, make_user
):
    rival = make_user(role=UserRole.COURSE_CREATOR)
    topic = make_topic(
        category=category,
        reserved_by=rival,
        reserved_until=timezone.localdate() + timedelta(days=5),
    )
    api_client.force_authenticate(creator)

    response = api_client.post(
        "/api/v1/courses/",
        _course_body(category, topic=str(topic.id)),
        format="json",
    )

    assert response.status_code == 400
    assert "reserved" in " ".join(
        error["message"] for error in response.json()["errors"]
    )


@pytest.mark.django_db
@pytest.mark.parametrize("switch", [False, True], ids=["off", "on"])
def test_an_inactive_topic_is_refused_whatever_the_switch_says(
    api_client, creator, category, switch
):
    if switch:
        platform_settings_service.update_settings(staged_review_flow_enabled=True)
    topic = make_topic(category=category, status=CategoryStatus.INACTIVE)
    api_client.force_authenticate(creator)

    response = api_client.post(
        "/api/v1/courses/",
        _course_body(category, topic=str(topic.id)),
        format="json",
    )

    assert response.status_code == 400
    assert "not currently available" in " ".join(
        error["message"] for error in response.json()["errors"]
    )
    assert not Course.objects.exists()


@pytest.mark.django_db
def test_off_a_course_without_a_topic_is_still_created(api_client, creator, category):
    api_client.force_authenticate(creator)

    response = api_client.post("/api/v1/courses/", _course_body(category), format="json")

    assert response.status_code == 201


# --- POST /course-ai-generations/ ---------------------------------------------


@pytest.mark.django_db
@patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
def test_on_an_ai_course_without_a_topic_is_refused_before_a_job_is_queued(
    delay, api_client, creator, category, staged_flow
):
    api_client.force_authenticate(creator)

    response = api_client.post(
        "/api/v1/course-ai-generations/", _ai_body(category), format="json"
    )

    assert response.status_code == 400
    assert "request one and wait" in _topic_error(response)["message"]
    assert not AIGenerationJob.objects.exists()
    delay.assert_not_called()


@pytest.mark.django_db
@patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
def test_on_an_ai_course_with_a_topic_is_queued(
    delay, api_client, creator, category, staged_flow
):
    delay.return_value.id = "task-id"
    topic = make_topic(category=category)
    api_client.force_authenticate(creator)

    response = api_client.post(
        "/api/v1/course-ai-generations/",
        _ai_body(category, topic=str(topic.id)),
        format="json",
    )

    assert response.status_code == 202


@pytest.mark.django_db
@patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
def test_off_an_ai_course_without_a_topic_is_still_queued(
    delay, api_client, creator, category
):
    delay.return_value.id = "task-id"
    api_client.force_authenticate(creator)

    response = api_client.post(
        "/api/v1/course-ai-generations/", _ai_body(category), format="json"
    )

    assert response.status_code == 202


# --- POST /course-imports/ ----------------------------------------------------


@pytest.mark.django_db
@patch("api.courses.services.course_import_service.StorageService.download_bytes")
def test_on_an_import_without_a_topic_is_refused_before_a_job_is_created(
    download, api_client, creator, category, staged_flow
):
    download.return_value = CSV_BYTES
    api_client.force_authenticate(creator)

    response = api_client.post(
        "/api/v1/course-imports/", _import_body(category), format="json"
    )

    assert response.status_code == 400
    assert "request one and wait" in _topic_error(response)["message"]
    assert not CourseImportJob.objects.exists()


@pytest.mark.django_db
@patch("api.courses.services.course_import_service.StorageService.download_bytes")
def test_on_an_import_with_a_topic_starts(
    download, api_client, creator, category, staged_flow
):
    download.return_value = CSV_BYTES
    topic = make_topic(category=category)
    api_client.force_authenticate(creator)

    response = api_client.post(
        "/api/v1/course-imports/",
        _import_body(category, topic=str(topic.id)),
        format="json",
    )

    assert response.status_code == 202


@pytest.mark.django_db
def test_a_developers_course_is_not_subject_to_the_topic_gate(
    creator, category, staged_flow
):
    """A developer's course is gated on its approved idea (the MIE push), so
    the creator-facing topic rule must not apply to it."""

    from api.courses.services import course_service

    course = course_service.create_draft_course(
        creator=creator,
        category=category,
        title="Pushed course",
        description="d" * 20,
        terms_accepted=True,
        source_type=CourseSourceType.DEVELOPER_API,
    )

    assert course.topic_id is None


# --- the requester hears the decision -----------------------------------------


@pytest.mark.django_db
def test_approving_a_topic_request_tells_the_requester(
    api_client, creator, category, make_user
):
    admin = make_user(role=UserRole.ADMIN)
    request = make_topic_reservation_request(requested_by=creator, category=category)
    api_client.force_authenticate(admin)

    response = api_client.post(f"/api/v1/topic-reservations/{request.id}/approve/")

    assert response.status_code == 200
    notification = Notification.objects.get(
        receiver=creator, title="Topic request approved"
    )
    assert request.name in notification.content
    assert notification.metadata["topic_reservation_request_id"] == str(request.id)


@pytest.mark.django_db
def test_declining_a_topic_request_tells_the_requester_why(
    api_client, creator, category, make_user
):
    admin = make_user(role=UserRole.ADMIN)
    request = make_topic_reservation_request(requested_by=creator, category=category)
    api_client.force_authenticate(admin)

    response = api_client.post(
        f"/api/v1/topic-reservations/{request.id}/reject/",
        {"reason": "Already covered by Python Basics"},
        format="json",
    )

    assert response.status_code == 200
    request.refresh_from_db()
    assert request.status == ReservationStatus.REJECTED
    notification = Notification.objects.get(
        receiver=creator, title="Topic request declined"
    )
    assert "Already covered by Python Basics" in notification.content
    assert request.name in notification.content


@pytest.mark.django_db
def test_a_request_that_cannot_be_decided_notifies_no_one(
    api_client, creator, category, make_user
):
    """A duplicate approval fails before anything is written, so the
    requester must not hear about a decision that did not happen."""

    admin = make_user(role=UserRole.ADMIN)
    request = make_topic_reservation_request(
        requested_by=creator, category=category, status=ReservationStatus.REJECTED
    )
    api_client.force_authenticate(admin)

    response = api_client.post(f"/api/v1/topic-reservations/{request.id}/approve/")

    assert response.status_code == 400
    assert not Notification.objects.filter(receiver=creator).exists()
