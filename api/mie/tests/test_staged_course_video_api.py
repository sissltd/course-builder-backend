"""A developer's course through the staged review flow.

The developer pushes the text only, a Writer approves it at the first seat,
the platform tells the developer to send the video (`COURSE_TEXT_APPROVED`),
and the developer sends it with `POST /mie/v1/submissions/{id}/course/video/`.
Every request goes to the literal URL a developer calls, with a real API key.
"""

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from api.catalog.enums import CategoryStatus
from api.courses.enums import CourseStatus, VideoProvider
from api.courses.models import Course, CourseVersion, Lesson
from api.courses.tests.factories import make_category, make_questions
from api.mie.enums import WebhookEventType
from api.mie.models import WebhookEvent
from api.mie.tests.factories import (
    make_approved_developer,
    make_decided_submission,
    make_system_developer,
)
from api.notification.models import Notification
from api.platform.services import platform_settings_service
from api.reviews.enums import ReviewStage
from api.users.enums import UserRole

QUEUE = "/api/v1/review-queue/"
PREVIEW_URL = "https://cdn.example.com/rust/preview.mp4"


def _push_url(submission) -> str:
    return f"/api/v1/mie/v1/submissions/{submission.id}/course/"


def _video_url(submission) -> str:
    return f"/api/v1/mie/v1/submissions/{submission.id}/course/video/"


@pytest.fixture
def staged_flow(db):
    platform_settings_service.update_settings(staged_review_flow_enabled=True)


@pytest.fixture
def category(db):
    return make_category(
        creator_price_intermediate=Decimal("150.00"), status=CategoryStatus.ACTIVE
    )


@pytest.fixture
def version(db):
    return CourseVersion.objects.get_or_create(label="1.0")[0]


@pytest.fixture
def writer(make_user):
    return make_user(role=UserRole.STAFF_WRITER)


@pytest.fixture
def other_writer(make_user):
    return make_user(role=UserRole.STAFF_WRITER)


@pytest.fixture
def verifier(make_user):
    return make_user(role=UserRole.STAFF_VERIFIER)


def _text_body(*, title, category, version, video_lessons=0, **overrides):
    """A push body that passes the text rules and carries no video. The first
    `video_lessons` lessons are VIDEO lessons: a script and no media yet."""

    lesson_index = 0
    modules = []
    for module_number in range(1, 5):
        lessons = []
        for lesson_number in range(1, 4):
            is_video = lesson_index < video_lessons
            lesson_index += 1
            lesson = {
                "title": f"Lesson {module_number}-{lesson_number}",
                "lesson_type": "VIDEO" if is_video else "TEXT",
                "script": "word " * 600,
                "learning_objectives": ["Objective 1", "Objective 2"],
                "duration_minutes": 20,
            }
            lessons.append(lesson)
        modules.append(
            {
                "title": f"Module {module_number}",
                "learning_objectives": ["Module objective"],
                "lessons": lessons,
                "assessment": {
                    "title": f"Module {module_number} quiz",
                    "questions": make_questions(3),
                },
            }
        )
    body = {
        "title": title,
        "description": "word " * 150,
        "category": str(category.id),
        "version": str(version.id),
        "difficulty_level": "INTERMEDIATE",
        "learning_objectives": [f"Course objective {i}" for i in range(1, 6)],
        "terms_accepted": True,
        "modules": modules,
        "final_assessment": {"title": "Final exam", "questions": make_questions(15)},
    }
    body.update(overrides)
    return body


def _video_body(*, video_lessons=0, **overrides):
    body = {
        "preview_video_url": PREVIEW_URL,
        "lessons": [
            {
                "module_order": 1 + index // 3,
                "lesson_order": 1 + index % 3,
                "video_url": f"https://cdn.example.com/rust/lesson-{index}.mp4",
            }
            for index in range(video_lessons)
        ],
    }
    body.update(overrides)
    return body


def _push(client, key, submission, body):
    return client.post(
        _push_url(submission), body, format="json", HTTP_X_MIE_API_KEY=key
    )


def _send_video(client, key, submission, body):
    return client.post(
        _video_url(submission), body, format="json", HTTP_X_MIE_API_KEY=key
    )


def _review(client, course, reviewer, *, approve=True, summary="Please fix this"):
    client.force_authenticate(reviewer)
    assert client.post(f"{QUEUE}{course.id}/claim/", format="json").status_code == 200
    if approve:
        response = client.post(f"{QUEUE}{course.id}/approve/", format="json")
    else:
        response = client.post(
            f"{QUEUE}{course.id}/reject/",
            {"feedback": {"summary": summary}},
            format="json",
        )
    assert response.status_code == 200
    client.force_authenticate(None)
    course.refresh_from_db()


def _events(submission, event_type):
    return WebhookEvent.objects.filter(submission=submission, event_type=event_type)


@pytest.fixture
def pushed(api_client, category, version, staged_flow):
    """An approved idea whose text-only course was pushed and is waiting at
    the first review seat. Returns (developer, key, idea, course)."""

    developer, key = make_approved_developer()
    idea = make_decided_submission(developer=developer, title="Build a Rust Course")
    response = _push(
        api_client,
        key,
        idea,
        _text_body(title=idea.title, category=category, version=version, video_lessons=2),
    )
    assert response.status_code == 201
    return developer, key, idea, Course.objects.get(pk=response.data["course_id"])


@pytest.fixture
def awaiting_video(api_client, pushed, writer):
    developer, key, idea, course = pushed
    _review(api_client, course, writer)
    return developer, key, idea, course


# --- pushing the text ---------------------------------------------------------


@pytest.mark.django_db
def test_on_a_text_only_push_is_submitted_to_the_first_seat(pushed):
    _, _, _, course = pushed

    assert course.status == CourseStatus.SUBMITTED
    assert course.review_stage == ReviewStage.CONTENT
    assert course.preview_video_url == ""


@pytest.mark.django_db
def test_on_a_video_lesson_may_be_pushed_as_a_script(pushed):
    _, _, _, course = pushed

    assert Lesson.objects.filter(
        module__course=course, content_type="VIDEO", video_url=""
    ).count() == 2


@pytest.mark.django_db
def test_on_a_push_carrying_a_preview_video_is_refused_and_stores_nothing(
    api_client, category, version, staged_flow
):
    developer, key = make_approved_developer()
    idea = make_decided_submission(developer=developer, title="Build a Rust Course")
    body = _text_body(
        title=idea.title,
        category=category,
        version=version,
        preview_video_url=PREVIEW_URL,
    )

    response = _push(api_client, key, idea, body)

    assert response.status_code == 400
    messages = " ".join(error["message"] for error in response.data["errors"])
    assert "Remove the preview video" in messages
    assert not Course.objects.exists()


# --- the first seat approves the text ----------------------------------------


@pytest.mark.django_db
def test_approving_a_developers_text_tells_the_developer_to_send_the_video(
    api_client, pushed, writer
):
    _, _, idea, course = pushed
    before = _events(idea, WebhookEventType.COURSE_TEXT_APPROVED).count()

    _review(api_client, course, writer)

    assert course.status == CourseStatus.AWAITING_VIDEO
    assert course.video_provider == VideoProvider.DEVELOPER
    events = _events(idea, WebhookEventType.COURSE_TEXT_APPROVED)
    assert events.count() == before + 1
    assert events.get().payload["submission"]["course"]["status"] == "AWAITING_VIDEO"
    # The developer's linked account is not a person to ask.
    assert not Notification.objects.filter(
        receiver=course.creator, title="Text approved - add your video?"
    ).exists()


@pytest.mark.django_db
def test_the_platforms_own_crawler_is_not_asked_for_a_video(
    api_client, category, version, staged_flow, writer
):
    developer, key = make_system_developer()
    idea = make_decided_submission(developer=developer, title="Crawled topic")
    response = _push(
        api_client,
        key,
        idea,
        _text_body(title=idea.title, category=category, version=version),
    )
    course = Course.objects.get(pk=response.data["course_id"])

    _review(api_client, course, writer)

    assert course.status == CourseStatus.AWAITING_VIDEO
    assert course.video_provider == VideoProvider.PRODUCTION_ENGINE
    assert not _events(idea, WebhookEventType.COURSE_TEXT_APPROVED).exists()


# --- POST /mie/v1/submissions/{id}/course/video/ ------------------------------


@pytest.mark.django_db
def test_the_developer_sends_the_video_and_the_course_goes_to_the_second_seat(
    api_client, awaiting_video
):
    _, key, idea, course = awaiting_video
    before = _events(idea, WebhookEventType.COURSE_SUBMITTED).count()

    response = _send_video(api_client, key, idea, _video_body(video_lessons=2))

    assert response.status_code == 200
    assert response.data["status"] == CourseStatus.SUBMITTED
    assert response.data["course_id"] == str(course.id)
    course.refresh_from_db()
    assert course.preview_video_url == PREVIEW_URL
    assert course.review_stage == ReviewStage.SECOND_REVIEW
    assert course.video_attached_at is not None
    assert (
        Lesson.objects.filter(module__course=course)
        .exclude(video_url="")
        .count()
        == 2
    )
    assert _events(idea, WebhookEventType.COURSE_SUBMITTED).count() == before + 1


@pytest.mark.django_db
def test_a_video_lesson_without_media_fails_the_check_and_stores_nothing(
    api_client, awaiting_video
):
    _, key, idea, course = awaiting_video

    response = _send_video(api_client, key, idea, _video_body(video_lessons=1))

    assert response.status_code == 400
    messages = " ".join(error["message"] for error in response.data["errors"])
    assert "requires a video_url or embedded_link" in messages
    course.refresh_from_db()
    assert course.status == CourseStatus.AWAITING_VIDEO
    assert course.preview_video_url == ""  # the transaction rolled back
    assert not Lesson.objects.filter(module__course=course).exclude(video_url="").exists()


@pytest.mark.django_db
def test_a_lesson_the_course_does_not_have_is_a_validation_error(
    api_client, awaiting_video
):
    _, key, idea, course = awaiting_video
    body = _video_body(video_lessons=2)
    body["lessons"].append({"module_order": 9, "lesson_order": 1, "video_url": PREVIEW_URL})

    response = _send_video(api_client, key, idea, body)

    assert response.status_code == 400
    assert response.data["errors"][0]["field_name"] == "lessons"
    course.refresh_from_db()
    assert course.status == CourseStatus.AWAITING_VIDEO


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.pop("preview_video_url"),
        lambda body: body["lessons"].append(dict(body["lessons"][0])),
        lambda body: body["lessons"][0].pop("video_url"),
    ],
    ids=["no preview video", "duplicate lesson address", "lesson without media"],
)
def test_a_malformed_video_body_is_a_400(api_client, awaiting_video, mutate):
    _, key, idea, _ = awaiting_video
    body = _video_body(video_lessons=2)
    mutate(body)

    response = _send_video(api_client, key, idea, body)

    assert response.status_code == 400


@pytest.mark.django_db
def test_a_course_still_in_the_first_seat_is_not_awaiting_video(api_client, pushed):
    _, key, idea, _ = pushed

    response = _send_video(api_client, key, idea, _video_body(video_lessons=2))

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "course_not_awaiting_video"


@pytest.mark.django_db
def test_an_idea_with_no_course_yet_is_not_awaiting_video(api_client, staged_flow):
    developer, key = make_approved_developer()
    idea = make_decided_submission(developer=developer)

    response = _send_video(api_client, key, idea, _video_body())

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "course_not_awaiting_video"


@pytest.mark.django_db
def test_the_crawlers_course_cannot_be_given_a_video_by_its_account(
    api_client, category, version, staged_flow, writer
):
    developer, key = make_system_developer()
    idea = make_decided_submission(developer=developer, title="Crawled topic")
    response = _push(
        api_client,
        key,
        idea,
        _text_body(title=idea.title, category=category, version=version),
    )
    course = Course.objects.get(pk=response.data["course_id"])
    _review(api_client, course, writer)

    refused = _send_video(api_client, key, idea, _video_body())

    assert refused.status_code == 409
    assert refused.data["errors"][0]["code"] == "video_provider_conflict"
    course.refresh_from_db()
    assert course.status == CourseStatus.AWAITING_VIDEO


@pytest.mark.django_db
def test_another_developers_idea_is_a_404(api_client, awaiting_video):
    _, _, idea, _ = awaiting_video
    _, other_key = make_approved_developer()

    response = _send_video(api_client, other_key, idea, _video_body(video_lessons=2))

    assert response.status_code == 404


@pytest.mark.django_db
def test_the_video_endpoint_needs_a_developer_key(api_client, awaiting_video):
    _, _, idea, _ = awaiting_video

    response = api_client.post(_video_url(idea), _video_body(), format="json")

    assert response.status_code == 401


# --- revisions ----------------------------------------------------------------


@pytest.mark.django_db
def test_a_developer_repushes_text_after_the_first_seat_sends_it_back(
    api_client, category, version, pushed, writer
):
    _, key, idea, course = pushed
    _review(api_client, course, writer, approve=False, summary="Add depth to module 2")
    assert course.status == CourseStatus.NEEDS_REVISION
    assert _events(idea, WebhookEventType.COURSE_REVISION_REQUESTED).exists()

    status_before = api_client.get(_push_url(idea), HTTP_X_MIE_API_KEY=key)
    assert status_before.data["revision_feedback"]["feedback"]["summary"] == (
        "Add depth to module 2"
    )

    response = _push(
        api_client,
        key,
        idea,
        _text_body(title=idea.title, category=category, version=version),
    )

    assert response.status_code == 201
    course.refresh_from_db()
    assert (course.status, course.review_stage) == (
        CourseStatus.SUBMITTED,
        ReviewStage.CONTENT,
    )


@pytest.mark.django_db
def test_a_push_while_the_course_is_in_review_is_still_refused(api_client, pushed, category, version):
    _, key, idea, _ = pushed

    response = _push(
        api_client,
        key,
        idea,
        _text_body(title=idea.title, category=category, version=version),
    )

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "course_in_review"


@pytest.mark.django_db
def test_a_video_the_verifier_sends_back_is_resent_and_resumes_at_verification(
    api_client, awaiting_video, other_writer, verifier, writer
):
    _, key, idea, course = awaiting_video
    assert _send_video(
        api_client, key, idea, _video_body(video_lessons=2)
    ).status_code == 200
    _review(api_client, course, other_writer)  # second seat: the video
    _review(api_client, course, verifier, approve=False, summary="Re-record lesson 1")
    assert (course.status, course.revision_seat) == (
        CourseStatus.NEEDS_REVISION,
        ReviewStage.VERIFICATION,
    )

    response = _send_video(
        api_client,
        key,
        idea,
        _video_body(
            video_lessons=2,
            preview_video_url="https://cdn.example.com/rust/preview-v2.mp4",
        ),
    )

    assert response.status_code == 200
    course.refresh_from_db()
    assert (course.status, course.review_stage) == (
        CourseStatus.SUBMITTED,
        ReviewStage.VERIFICATION,
    )
    assert course.preview_video_url.endswith("preview-v2.mp4")


# --- cost ---------------------------------------------------------------------


def _video_request_query_count(api_client, category, version, writer, lessons):
    developer, key = make_approved_developer()
    idea = make_decided_submission(developer=developer, title=f"Topic {lessons}")
    response = _push(
        api_client,
        key,
        idea,
        _text_body(
            title=idea.title,
            category=category,
            version=version,
            video_lessons=lessons,
        ),
    )
    course = Course.objects.get(pk=response.data["course_id"])
    _review(api_client, course, writer)
    with CaptureQueriesContext(connection) as queries:
        sent = _send_video(
            api_client, key, idea, _video_body(video_lessons=lessons)
        )
    assert sent.status_code == 200
    return len(queries)


@pytest.mark.django_db
def test_the_video_request_costs_the_same_however_many_lessons_it_carries(
    api_client, category, version, staged_flow, make_user
):
    few = _video_request_query_count(
        api_client, category, version, make_user(role=UserRole.STAFF_WRITER), 1
    )
    many = _video_request_query_count(
        api_client, category, version, make_user(role=UserRole.STAFF_WRITER), 9
    )

    assert few == many
