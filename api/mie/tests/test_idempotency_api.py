"""The optional Idempotency-Key header on the developer POSTs.

A retry with the same key and body replays the first successful response
instead of running again; without the header every POST runs as before.
Every request goes to the literal URL a developer calls, with a real key.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from api.catalog.enums import CategoryStatus
from api.courses.models import Course, CourseVersion
from api.courses.tests.factories import make_category, make_questions
from api.mie.enums import WebhookEventType
from api.mie.models import CourseSubmission, IdempotencyKey, WebhookEvent
from api.mie.services import idempotency_service
from api.mie.tasks import purge_expired_idempotency_keys_task
from api.mie.tests.factories import make_approved_developer, make_decided_submission
from api.platform.services import platform_settings_service
from api.users.enums import UserRole

INGEST = "/api/v1/mie/v1/submissions/"
QUEUE = "/api/v1/review-queue/"


def _push_url(submission) -> str:
    return f"/api/v1/mie/v1/submissions/{submission.id}/course/"


def _video_url(submission) -> str:
    return f"/api/v1/mie/v1/submissions/{submission.id}/course/video/"


def _submit(client, key, body, idempotency_key=None):
    headers = {"HTTP_X_MIE_API_KEY": key}
    if idempotency_key is not None:
        headers["HTTP_IDEMPOTENCY_KEY"] = idempotency_key
    return client.post(INGEST, body, format="json", **headers)


def _post(client, url, key, body, idempotency_key):
    return client.post(
        url,
        body,
        format="json",
        HTTP_X_MIE_API_KEY=key,
        HTTP_IDEMPOTENCY_KEY=idempotency_key,
    )


@pytest.fixture
def developer(db):
    return make_approved_developer()


@pytest.fixture
def staged_flow(db):
    platform_settings_service.update_settings(staged_review_flow_enabled=True)


@pytest.fixture
def category(db):
    return make_category(
        creator_price_with_video=Decimal("150.00"), status=CategoryStatus.ACTIVE
    )


@pytest.fixture
def version(db):
    return CourseVersion.objects.get_or_create(label="1.0")[0]


def _text_body(*, title, category, version):
    modules = []
    for module_number in range(1, 5):
        modules.append(
            {
                "title": f"Module {module_number}",
                "learning_objectives": ["Module objective"],
                "lessons": [
                    {
                        "title": f"Lesson {module_number}-{lesson_number}",
                        "lesson_type": "VIDEO"
                        if module_number == 1 and lesson_number == 1
                        else "TEXT",
                        "script": "word " * 600,
                        "learning_objectives": ["Objective 1", "Objective 2"],
                        "duration_minutes": 20,
                    }
                    for lesson_number in range(1, 4)
                ],
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


VIDEO_BODY = {
    "preview_video_url": "https://cdn.example.com/rust/preview.mp4",
    "lessons": [
        {
            "module_order": 1,
            "lesson_order": 1,
            "video_url": "https://cdn.example.com/rust/m1l1.mp4",
        }
    ],
}


# --- submissions --------------------------------------------------------------


@pytest.mark.django_db
def test_a_retry_with_the_same_key_replays_the_first_submission(api_client, developer):
    account, key = developer
    body = {"title": "Kubernetes cost optimisation"}

    first = _submit(api_client, key, body, idempotency_key="idea-1")
    retry = _submit(api_client, key, body, idempotency_key="idea-1")

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    assert retry["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first
    assert CourseSubmission.objects.filter(developer=account).count() == 1
    assert WebhookEvent.objects.filter(submission__developer=account).count() == 1


@pytest.mark.django_db
def test_without_a_key_every_submission_still_runs(api_client, developer):
    account, key = developer
    body = {"title": "Kubernetes cost optimisation"}

    _submit(api_client, key, body)
    _submit(api_client, key, body)

    assert CourseSubmission.objects.filter(developer=account).count() == 2
    assert not IdempotencyKey.objects.exists()


@pytest.mark.django_db
def test_body_key_order_does_not_change_the_request(api_client, developer):
    account, key = developer

    _submit(api_client, key, {"title": "Rust", "description": "Ownership"}, "idea-1")
    retry = _submit(
        api_client, key, {"description": "Ownership", "title": "Rust"}, "idea-1"
    )

    assert retry.status_code == 201
    assert retry["Idempotent-Replayed"] == "true"
    assert CourseSubmission.objects.filter(developer=account).count() == 1


@pytest.mark.django_db
def test_the_same_key_with_a_different_body_is_a_409(api_client, developer):
    account, key = developer
    _submit(api_client, key, {"title": "Rust"}, idempotency_key="idea-1")

    response = _submit(api_client, key, {"title": "Go"}, idempotency_key="idea-1")

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "idempotency_key_reused"
    assert CourseSubmission.objects.filter(developer=account).count() == 1


@pytest.mark.django_db
def test_the_same_key_on_another_endpoint_is_a_409(
    api_client, developer, category, version, staged_flow
):
    account, key = developer
    idea = make_decided_submission(developer=account, title="Build a Rust Course")
    _submit(api_client, key, {"title": "Rust"}, idempotency_key="shared")

    response = _post(
        api_client,
        _push_url(idea),
        key,
        _text_body(title=idea.title, category=category, version=version),
        "shared",
    )

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "idempotency_key_reused"
    assert not Course.objects.exists()


@pytest.mark.django_db
def test_a_failed_request_does_not_use_up_its_key(api_client, developer):
    account, key = developer

    failed = _submit(api_client, key, {"title": ""}, idempotency_key="idea-1")
    fixed = _submit(api_client, key, {"title": "Rust"}, idempotency_key="idea-1")

    assert failed.status_code == 400
    assert fixed.status_code == 201
    assert "Idempotent-Replayed" not in fixed
    assert CourseSubmission.objects.filter(developer=account).count() == 1


@pytest.mark.django_db
def test_an_error_response_does_not_use_up_its_key_either(api_client, developer):
    # A non-object body is answered with a 400 the view returns, not raises.
    account, key = developer

    failed = _submit(api_client, key, ["Rust"], idempotency_key="idea-1")
    fixed = _submit(api_client, key, {"title": "Rust"}, idempotency_key="idea-1")

    assert failed.status_code == 400
    assert fixed.status_code == 201
    assert CourseSubmission.objects.filter(developer=account).count() == 1


@pytest.mark.django_db
def test_a_retry_while_the_first_request_runs_is_a_409(api_client, developer):
    account, key = developer
    body = {"title": "Rust"}
    IdempotencyKey.objects.create(
        developer=account,
        key="idea-1",
        scope=f"POST {INGEST}",
        request_hash=idempotency_service.fingerprint(body),
    )

    response = _submit(api_client, key, body, idempotency_key="idea-1")

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "idempotency_request_in_progress"
    assert not CourseSubmission.objects.exists()


@pytest.mark.django_db
def test_an_abandoned_claim_is_taken_over_after_its_lease(api_client, developer):
    account, key = developer
    body = {"title": "Rust"}
    record = IdempotencyKey.objects.create(
        developer=account,
        key="idea-1",
        scope=f"POST {INGEST}",
        request_hash=idempotency_service.fingerprint(body),
    )
    stale = timezone.now() - idempotency_service.IN_FLIGHT_LEASE - timedelta(seconds=1)
    IdempotencyKey.objects.filter(pk=record.pk).update(updated_datetime=stale)

    response = _submit(api_client, key, body, idempotency_key="idea-1")

    assert response.status_code == 201
    assert CourseSubmission.objects.filter(developer=account).count() == 1
    record.refresh_from_db()
    assert record.completed_at is not None


@pytest.mark.django_db
def test_an_expired_key_runs_as_a_new_request(api_client, developer):
    account, key = developer
    body = {"title": "Rust"}
    _submit(api_client, key, body, idempotency_key="idea-1")
    expired = timezone.now() - idempotency_service.KEY_TTL - timedelta(seconds=1)
    IdempotencyKey.objects.update(created_datetime=expired)

    response = _submit(api_client, key, body, idempotency_key="idea-1")

    assert response.status_code == 201
    assert "Idempotent-Replayed" not in response
    assert CourseSubmission.objects.filter(developer=account).count() == 2


@pytest.mark.django_db
def test_keys_belong_to_one_developer(api_client, developer):
    account, key = developer
    other_account, other_key = make_approved_developer()

    _submit(api_client, key, {"title": "Rust"}, idempotency_key="idea-1")
    theirs = _submit(api_client, other_key, {"title": "Go"}, idempotency_key="idea-1")

    assert theirs.status_code == 201
    assert "Idempotent-Replayed" not in theirs
    assert CourseSubmission.objects.filter(developer=other_account).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("bad_key", ["", "has spaces", "x" * 256, "naïve"])
def test_a_malformed_key_is_a_400(api_client, developer, bad_key):
    account, key = developer

    response = _submit(api_client, key, {"title": "Rust"}, idempotency_key=bad_key)

    assert response.status_code == 400
    assert response.data["errors"][0]["field_name"] == "Idempotency-Key"
    assert not CourseSubmission.objects.exists()
    assert not IdempotencyKey.objects.exists()


# --- course and video pushes --------------------------------------------------


@pytest.mark.django_db
def test_a_retried_course_push_replays_instead_of_conflicting(
    api_client, developer, category, version, staged_flow
):
    account, key = developer
    idea = make_decided_submission(developer=account, title="Build a Rust Course")
    body = _text_body(title=idea.title, category=category, version=version)

    first = _post(api_client, _push_url(idea), key, body, "push-1")
    retry = _post(api_client, _push_url(idea), key, body, "push-1")

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    assert retry["Idempotent-Replayed"] == "true"
    assert Course.objects.count() == 1
    assert (
        WebhookEvent.objects.filter(
            submission=idea, event_type=WebhookEventType.COURSE_SUBMITTED
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_a_retried_video_push_replays_instead_of_conflicting(
    api_client, developer, category, version, staged_flow, make_user
):
    account, key = developer
    idea = make_decided_submission(developer=account, title="Build a Rust Course")
    pushed = _post(
        api_client,
        _push_url(idea),
        key,
        _text_body(title=idea.title, category=category, version=version),
        "push-1",
    )
    course = Course.objects.get(pk=pushed.data["course_id"])
    api_client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))
    assert (
        api_client.post(f"{QUEUE}{course.id}/claim/", format="json").status_code == 200
    )
    assert (
        api_client.post(f"{QUEUE}{course.id}/approve/", format="json").status_code
        == 200
    )
    api_client.force_authenticate(None)

    first = _post(api_client, _video_url(idea), key, VIDEO_BODY, "video-1")
    retry = _post(api_client, _video_url(idea), key, VIDEO_BODY, "video-1")

    assert first.status_code == retry.status_code == 200
    assert retry.json() == first.json()
    assert retry["Idempotent-Replayed"] == "true"
    # Without the key the retry would have been refused: the course moved on.
    unkeyed = api_client.post(
        _video_url(idea), VIDEO_BODY, format="json", HTTP_X_MIE_API_KEY=key
    )
    assert unkeyed.status_code == 409


@pytest.mark.django_db
def test_a_key_needs_a_developer_key_first(api_client):
    response = api_client.post(
        INGEST, {"title": "Rust"}, format="json", HTTP_IDEMPOTENCY_KEY="idea-1"
    )

    assert response.status_code == 401
    assert not IdempotencyKey.objects.exists()


# --- housekeeping -------------------------------------------------------------


@pytest.mark.django_db
def test_the_purge_task_deletes_only_expired_keys(api_client, developer):
    _, key = developer
    _submit(api_client, key, {"title": "Old"}, idempotency_key="old")
    _submit(api_client, key, {"title": "New"}, idempotency_key="new")
    expired = timezone.now() - idempotency_service.KEY_TTL - timedelta(seconds=1)
    IdempotencyKey.objects.filter(key="old").update(created_datetime=expired)

    deleted = purge_expired_idempotency_keys_task()

    assert deleted == 1
    assert list(IdempotencyKey.objects.values_list("key", flat=True)) == ["new"]
