"""Admin moves on a developer's course, and the events that tell the developer.

* An account can require video assignment: once its text passes, the course
  waits with no producer until an admin assigns the video to the developer
  or to the production engine (`COURSE_VIDEO_ASSIGNED`).
* Every reviewer rejection of a developer's course fires `COURSE_REJECTED`
  before `COURSE_REVISION_REQUESTED`.
* An admin can archive a developer's course for good (`COURSE_ARCHIVED`).

Every request goes to the literal URL its caller uses.
"""

from decimal import Decimal

import pytest

from api.catalog.enums import CategoryStatus
from api.courses.enums import CourseStatus, VideoProvider
from api.courses.models import Course, CourseVersion
from api.courses.tests.factories import make_category, make_questions
from api.mie.enums import WebhookEventType
from api.mie.models import WebhookEvent
from api.mie.tests.factories import (
    make_approved_developer,
    make_decided_submission,
    make_system_developer,
)
from api.platform.services import platform_settings_service
from api.production.enums import ACTIVE_RUN_STATUSES, ProductionRunStatus
from api.production.models import ProductionRun
from api.users.enums import UserActivityActionEnums, UserRole
from api.users.models import UserActivityLog

QUEUE = "/api/v1/review-queue/"
PREVIEW_URL = "https://cdn.example.com/rust/preview.mp4"


def _push_url(submission) -> str:
    return f"/api/v1/mie/v1/submissions/{submission.id}/course/"


def _video_url(submission) -> str:
    return f"/api/v1/mie/v1/submissions/{submission.id}/course/video/"


def _assign_url(submission) -> str:
    return f"/api/v1/mie/admin/submissions/{submission.id}/course/video-assignment/"


def _archive_url(submission) -> str:
    return f"/api/v1/mie/admin/submissions/{submission.id}/course/archive/"


def _setting_url(developer) -> str:
    return f"/api/v1/mie/admin/developers/{developer.id}/video-assignment/"


@pytest.fixture
def staged_flow(db):
    platform_settings_service.update_settings(staged_review_flow_enabled=True)


@pytest.fixture
def category(db):
    return make_category(
        creator_price_with_video=Decimal("150.00"),
        creator_price_without_video=Decimal("90.00"),
        status=CategoryStatus.ACTIVE,
    )


@pytest.fixture
def version(db):
    return CourseVersion.objects.get_or_create(label="1.0")[0]


@pytest.fixture
def writer(make_user):
    return make_user(role=UserRole.STAFF_WRITER)


@pytest.fixture
def superadmin(make_user):
    return make_user(role=UserRole.SUPER_ADMIN)


@pytest.fixture
def creator(make_user):
    return make_user(role=UserRole.COURSE_CREATOR)


def _text_body(*, title, category, version, video_lessons=2):
    lesson_index = 0
    modules = []
    for module_number in range(1, 5):
        lessons = []
        for lesson_number in range(1, 4):
            is_video = lesson_index < video_lessons
            lesson_index += 1
            lessons.append(
                {
                    "title": f"Lesson {module_number}-{lesson_number}",
                    "lesson_type": "VIDEO" if is_video else "TEXT",
                    "script": "word " * 600,
                    "learning_objectives": ["Objective 1", "Objective 2"],
                    "duration_minutes": 20,
                }
            )
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
    return {
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


def _video_body(*, video_lessons=2):
    return {
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


def _push(client, key, submission, body):
    return client.post(
        _push_url(submission), body, format="json", HTTP_X_MIE_API_KEY=key
    )


def _send_video(client, key, submission):
    return client.post(
        _video_url(submission), _video_body(), format="json", HTTP_X_MIE_API_KEY=key
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


def _as(client, user):
    client.force_authenticate(user)
    return client


def _events(submission, event_type):
    return WebhookEvent.objects.filter(
        submission=submission, event_type=event_type
    ).order_by("created_datetime")


def _pushed_course(
    client, category, version, *, requires_assignment=False, system=False
):
    if system:
        developer, key = make_system_developer()
    else:
        developer, key = make_approved_developer(
            requires_video_assignment=requires_assignment
        )
    idea = make_decided_submission(developer=developer, title=f"Rust {developer.id}")
    response = _push(
        client,
        key,
        idea,
        _text_body(title=idea.title, category=category, version=version),
    )
    assert response.status_code == 201
    return developer, key, idea, Course.objects.get(pk=response.data["course_id"])


@pytest.fixture
def pushed(api_client, category, version, staged_flow):
    """A course waiting at the first seat, from an account that does not
    require assignment. Returns (developer, key, idea, course)."""

    return _pushed_course(api_client, category, version)


@pytest.fixture
def unassigned(api_client, category, version, staged_flow, writer):
    """A course whose text passed, from an account that requires assignment:
    AWAITING_VIDEO with no producer yet."""

    developer, key, idea, course = _pushed_course(
        api_client, category, version, requires_assignment=True
    )
    _review(api_client, course, writer)
    return developer, key, idea, course


# --- the account setting ------------------------------------------------------


@pytest.mark.django_db
def test_a_superadmin_turns_video_assignment_on(api_client, superadmin):
    developer, _ = make_approved_developer()

    response = _as(api_client, superadmin).post(
        _setting_url(developer), {"requires_video_assignment": True}, format="json"
    )

    assert response.status_code == 200
    assert response.data["id"] == str(developer.id)
    assert response.data["requires_video_assignment"] is True
    developer.refresh_from_db()
    assert developer.requires_video_assignment is True
    assert UserActivityLog.objects.filter(
        user=superadmin, action=UserActivityActionEnums.DEVELOPER_SETTINGS_UPDATED
    ).exists()


@pytest.mark.django_db
def test_setting_the_value_an_account_already_has_logs_nothing(api_client, superadmin):
    developer, _ = make_approved_developer()

    response = _as(api_client, superadmin).post(
        _setting_url(developer), {"requires_video_assignment": False}, format="json"
    )

    assert response.status_code == 200
    assert not UserActivityLog.objects.filter(
        action=UserActivityActionEnums.DEVELOPER_SETTINGS_UPDATED
    ).exists()


@pytest.mark.django_db
def test_the_crawler_account_cannot_require_assignment(api_client, superadmin):
    developer, _ = make_system_developer()

    response = _as(api_client, superadmin).post(
        _setting_url(developer), {"requires_video_assignment": True}, format="json"
    )

    assert response.status_code == 400
    assert response.data["errors"][0]["field_name"] == "requires_video_assignment"
    developer.refresh_from_db()
    assert developer.requires_video_assignment is False


@pytest.mark.django_db
def test_the_setting_needs_its_field(api_client, superadmin):
    developer, _ = make_approved_developer()

    response = _as(api_client, superadmin).post(
        _setting_url(developer), {}, format="json"
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_the_setting_404s_for_an_unknown_account(api_client, superadmin):
    developer, _ = make_approved_developer()
    url = _setting_url(developer)
    developer.delete()

    response = _as(api_client, superadmin).post(
        url, {"requires_video_assignment": True}, format="json"
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_the_setting_is_refused_to_anyone_without_the_console(api_client, creator):
    developer, _ = make_approved_developer()

    anonymous = api_client.post(
        _setting_url(developer), {"requires_video_assignment": True}, format="json"
    )
    forbidden = _as(api_client, creator).post(
        _setting_url(developer), {"requires_video_assignment": True}, format="json"
    )

    assert anonymous.status_code == 401
    assert forbidden.status_code == 403
    developer.refresh_from_db()
    assert developer.requires_video_assignment is False


@pytest.mark.django_db
def test_the_developer_sees_the_setting_on_me(api_client):
    _, key = make_approved_developer(requires_video_assignment=True)

    response = api_client.get("/api/v1/mie/v1/me/", HTTP_X_MIE_API_KEY=key)

    assert response.status_code == 200
    assert response.data["requires_video_assignment"] is True


# --- text approval ------------------------------------------------------------


@pytest.mark.django_db
def test_an_account_without_the_setting_is_still_sent_straight_to_the_video(
    api_client, pushed, writer
):
    _, key, idea, course = pushed

    _review(api_client, course, writer)

    assert course.video_provider == VideoProvider.DEVELOPER
    event = _events(idea, WebhookEventType.COURSE_TEXT_APPROVED).get()
    assert event.payload["submission"]["course"]["video_producer"] == "DEVELOPER"
    assert _send_video(api_client, key, idea).status_code == 200


@pytest.mark.django_db
def test_with_the_setting_the_passed_text_waits_with_no_producer(unassigned):
    _, _, idea, course = unassigned

    assert course.status == CourseStatus.AWAITING_VIDEO
    assert course.video_provider == ""
    event = _events(idea, WebhookEventType.COURSE_TEXT_APPROVED).get()
    assert event.payload["submission"]["course"]["video_producer"] is None
    assert not ProductionRun.objects.filter(course=course).exists()


@pytest.mark.django_db
def test_an_unassigned_video_cannot_be_sent(api_client, unassigned):
    _, key, idea, course = unassigned

    response = _send_video(api_client, key, idea)

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "video_not_assigned"
    course.refresh_from_db()
    assert course.status == CourseStatus.AWAITING_VIDEO
    assert course.preview_video_url == ""


# --- POST .../course/video-assignment/ ----------------------------------------


@pytest.mark.django_db
def test_assigning_the_video_to_the_developer_releases_their_video_push(
    api_client, unassigned, superadmin
):
    _, key, idea, course = unassigned

    response = _as(api_client, superadmin).post(
        _assign_url(idea), {"video_producer": "DEVELOPER"}, format="json"
    )

    assert response.status_code == 200
    assert response.data == {
        "submission_id": str(idea.id),
        "submission_reference": idea.public_reference,
        "course_id": str(course.id),
        "title": course.title,
        "status": CourseStatus.AWAITING_VIDEO,
        "video_producer": "DEVELOPER",
    }
    course.refresh_from_db()
    assert course.video_provider == VideoProvider.DEVELOPER
    event = _events(idea, WebhookEventType.COURSE_VIDEO_ASSIGNED).get()
    assert event.payload["submission"]["course"]["video_producer"] == "DEVELOPER"
    assert UserActivityLog.objects.filter(
        user=superadmin, action=UserActivityActionEnums.COURSE_VIDEO_ASSIGNED
    ).exists()

    api_client.force_authenticate(None)
    assert _send_video(api_client, key, idea).status_code == 200


@pytest.mark.django_db
def test_assigning_the_video_to_the_engine_queues_a_run_and_refuses_the_developer(
    api_client, unassigned, superadmin
):
    _, key, idea, course = unassigned

    response = _as(api_client, superadmin).post(
        _assign_url(idea), {"video_producer": "PRODUCTION_ENGINE"}, format="json"
    )

    assert response.status_code == 200
    assert response.data["video_producer"] == "PRODUCTION_ENGINE"
    course.refresh_from_db()
    assert course.video_provider == VideoProvider.PRODUCTION_ENGINE
    # Engine-made video is paid at the without-video price.
    assert course.creator_price_snapshot == Decimal("90.00")
    assert ProductionRun.objects.filter(
        course=course, status__in=ACTIVE_RUN_STATUSES
    ).exists()
    event = _events(idea, WebhookEventType.COURSE_VIDEO_ASSIGNED).get()
    assert (
        event.payload["submission"]["course"]["video_producer"] == "PRODUCTION_ENGINE"
    )

    api_client.force_authenticate(None)
    refused = _send_video(api_client, key, idea)
    assert refused.status_code == 409
    assert refused.data["errors"][0]["code"] == "video_provider_conflict"


@pytest.mark.django_db
def test_reassigning_from_the_engine_to_the_developer_cancels_the_run(
    api_client, unassigned, superadmin
):
    _, _, idea, course = unassigned
    client = _as(api_client, superadmin)
    client.post(
        _assign_url(idea), {"video_producer": "PRODUCTION_ENGINE"}, format="json"
    )

    response = client.post(
        _assign_url(idea), {"video_producer": "DEVELOPER"}, format="json"
    )

    assert response.status_code == 200
    run = ProductionRun.objects.get(course=course)
    assert run.status == ProductionRunStatus.CANCELLED
    course.refresh_from_db()
    assert course.creator_price_snapshot == Decimal("150.00")
    producers = [
        event.payload["submission"]["course"]["video_producer"]
        for event in _events(idea, WebhookEventType.COURSE_VIDEO_ASSIGNED)
    ]
    assert producers == ["PRODUCTION_ENGINE", "DEVELOPER"]


@pytest.mark.django_db
def test_an_account_without_the_setting_can_still_be_reassigned_to_the_engine(
    api_client, pushed, writer, superadmin
):
    _, _, idea, course = pushed
    _review(api_client, course, writer)

    response = _as(api_client, superadmin).post(
        _assign_url(idea), {"video_producer": "PRODUCTION_ENGINE"}, format="json"
    )

    assert response.status_code == 200
    course.refresh_from_db()
    assert course.video_provider == VideoProvider.PRODUCTION_ENGINE


@pytest.mark.django_db
def test_assigning_the_producer_a_course_already_has_is_a_400_and_fires_nothing(
    api_client, pushed, writer, superadmin
):
    _, _, idea, course = pushed
    _review(api_client, course, writer)

    response = _as(api_client, superadmin).post(
        _assign_url(idea), {"video_producer": "DEVELOPER"}, format="json"
    )

    assert response.status_code == 400
    assert response.data["errors"][0]["field_name"] == "video_producer"
    assert not _events(idea, WebhookEventType.COURSE_VIDEO_ASSIGNED).exists()


@pytest.mark.django_db
def test_a_course_still_in_review_cannot_have_its_video_assigned(
    api_client, pushed, superadmin
):
    _, _, idea, course = pushed

    response = _as(api_client, superadmin).post(
        _assign_url(idea), {"video_producer": "PRODUCTION_ENGINE"}, format="json"
    )

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "course_not_awaiting_video"
    course.refresh_from_db()
    assert course.video_provider == ""


@pytest.mark.django_db
def test_the_crawlers_course_cannot_be_handed_to_its_developer(
    api_client, category, version, staged_flow, writer, superadmin
):
    _, _, idea, course = _pushed_course(api_client, category, version, system=True)
    _review(api_client, course, writer)

    response = _as(api_client, superadmin).post(
        _assign_url(idea), {"video_producer": "DEVELOPER"}, format="json"
    )

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "video_provider_conflict"
    course.refresh_from_db()
    assert course.video_provider == VideoProvider.PRODUCTION_ENGINE


@pytest.mark.django_db
def test_an_unknown_producer_is_a_400(api_client, unassigned, superadmin):
    _, _, idea, _ = unassigned

    response = _as(api_client, superadmin).post(
        _assign_url(idea), {"video_producer": "CREATOR"}, format="json"
    )

    assert response.status_code == 400
    assert response.data["errors"][0]["field_name"] == "video_producer"


@pytest.mark.django_db
def test_an_idea_with_no_course_has_no_video_to_assign(
    api_client, superadmin, staged_flow
):
    developer, _ = make_approved_developer()
    idea = make_decided_submission(developer=developer)

    response = _as(api_client, superadmin).post(
        _assign_url(idea), {"video_producer": "DEVELOPER"}, format="json"
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_video_assignment_is_refused_to_anyone_without_the_console(
    api_client, unassigned, creator
):
    _, _, idea, course = unassigned

    anonymous = api_client.post(
        _assign_url(idea), {"video_producer": "DEVELOPER"}, format="json"
    )
    forbidden = _as(api_client, creator).post(
        _assign_url(idea), {"video_producer": "DEVELOPER"}, format="json"
    )

    assert anonymous.status_code == 401
    assert forbidden.status_code == 403
    course.refresh_from_db()
    assert course.video_provider == ""


# --- rejection ----------------------------------------------------------------


@pytest.mark.django_db
def test_a_rejection_fires_course_rejected_then_revision_requested(
    api_client, pushed, writer
):
    _, _, idea, course = pushed

    _review(api_client, course, writer, approve=False, summary="Add depth to module 2")

    events = list(
        WebhookEvent.objects.filter(
            submission=idea,
            event_type__in=(
                WebhookEventType.COURSE_REJECTED,
                WebhookEventType.COURSE_REVISION_REQUESTED,
            ),
        ).order_by("created_datetime")
    )
    assert [event.event_type for event in events] == [
        WebhookEventType.COURSE_REJECTED,
        WebhookEventType.COURSE_REVISION_REQUESTED,
    ]
    for event in events:
        feedback = event.payload["submission"]["course"]["revision_feedback"]
        assert feedback["feedback"]["summary"] == "Add depth to module 2"


@pytest.mark.django_db
def test_an_endpoint_subscribed_to_revisions_only_does_not_get_course_rejected(
    api_client, pushed, writer
):
    developer, _, idea, course = pushed
    endpoint = developer.webhook_endpoints.get()
    endpoint.all_events = False
    endpoint.event_types = [WebhookEventType.COURSE_REVISION_REQUESTED]
    endpoint.save()

    _review(api_client, course, writer, approve=False)

    assert _events(idea, WebhookEventType.COURSE_REVISION_REQUESTED).exists()
    assert not _events(idea, WebhookEventType.COURSE_REJECTED).exists()


# --- POST .../course/archive/ -------------------------------------------------


@pytest.mark.django_db
def test_archiving_a_course_ends_it_and_tells_the_developer_why(
    api_client, pushed, superadmin, category, version, writer
):
    _, key, idea, course = pushed

    response = _as(api_client, superadmin).post(
        _archive_url(idea), {"reason": "Repeats our Rust track."}, format="json"
    )

    assert response.status_code == 200
    assert response.data["status"] == CourseStatus.ARCHIVED
    assert response.data["course_id"] == str(course.id)
    course.refresh_from_db()
    assert (course.status, course.review_stage) == (CourseStatus.ARCHIVED, "")
    event = _events(idea, WebhookEventType.COURSE_ARCHIVED).get()
    assert (
        event.payload["submission"]["course"]["archive_reason"]
        == "Repeats our Rust track."
    )
    assert event.payload["submission"]["course"]["status"] == CourseStatus.ARCHIVED
    assert UserActivityLog.objects.filter(
        user=superadmin, action=UserActivityActionEnums.COURSE_ARCHIVED
    ).exists()

    # It has left the review queue...
    _as(api_client, writer)
    assert (
        api_client.post(f"{QUEUE}{course.id}/claim/", format="json").status_code != 200
    )
    # ...and can never be pushed again.
    api_client.force_authenticate(None)
    repush = _push(
        api_client,
        key,
        idea,
        _text_body(title=idea.title, category=category, version=version),
    )
    assert repush.status_code == 409
    assert repush.data["errors"][0]["code"] == "course_archived"


@pytest.mark.django_db
def test_archiving_a_course_the_engine_is_making_cancels_the_run(
    api_client, unassigned, superadmin
):
    _, _, idea, course = unassigned
    client = _as(api_client, superadmin)
    client.post(
        _assign_url(idea), {"video_producer": "PRODUCTION_ENGINE"}, format="json"
    )

    response = client.post(
        _archive_url(idea), {"reason": "Out of scope."}, format="json"
    )

    assert response.status_code == 200
    assert (
        ProductionRun.objects.get(course=course).status == ProductionRunStatus.CANCELLED
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status", [CourseStatus.ARCHIVED, CourseStatus.APPROVED, CourseStatus.PUBLISHED]
)
def test_a_course_past_archiving_is_a_409(api_client, pushed, superadmin, status):
    _, _, idea, course = pushed
    Course.objects.filter(pk=course.pk).update(status=status)

    response = _as(api_client, superadmin).post(
        _archive_url(idea), {"reason": "Out of scope."}, format="json"
    )

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "course_not_archivable"
    course.refresh_from_db()
    assert course.status == status
    assert not _events(idea, WebhookEventType.COURSE_ARCHIVED).exists()


@pytest.mark.django_db
def test_archiving_needs_a_reason(api_client, pushed, superadmin):
    _, _, idea, course = pushed

    response = _as(api_client, superadmin).post(_archive_url(idea), {}, format="json")

    assert response.status_code == 400
    assert response.data["errors"][0]["field_name"] == "reason"
    course.refresh_from_db()
    assert course.status != CourseStatus.ARCHIVED


@pytest.mark.django_db
def test_an_idea_with_no_course_has_nothing_to_archive(
    api_client, superadmin, staged_flow
):
    developer, _ = make_approved_developer()
    idea = make_decided_submission(developer=developer)

    response = _as(api_client, superadmin).post(
        _archive_url(idea), {"reason": "Out of scope."}, format="json"
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_archiving_is_refused_to_anyone_without_the_console(
    api_client, pushed, creator
):
    _, _, idea, course = pushed

    anonymous = api_client.post(_archive_url(idea), {"reason": "x"}, format="json")
    forbidden = _as(api_client, creator).post(
        _archive_url(idea), {"reason": "x"}, format="json"
    )

    assert anonymous.status_code == 401
    assert forbidden.status_code == 403
    course.refresh_from_db()
    assert course.status != CourseStatus.ARCHIVED
