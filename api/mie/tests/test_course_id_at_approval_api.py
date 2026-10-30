"""The course id a developer is given when their idea is approved.

The first approval reserves the id and SUBMISSION_APPROVED carries it, with
the submission id, title and category id; the first course push creates the
course under it, and no later decision changes it.

Every request goes to the literal URL its caller uses.
"""

import importlib
import uuid
from decimal import Decimal

import pytest
from django.apps import apps

from api.catalog.enums import CategoryStatus
from api.courses.models import Course, CourseVersion
from api.courses.tests.factories import (
    make_category,
    make_draft_course,
    make_questions,
)
from api.mie.enums import SubmissionStatus, WebhookEventType
from api.mie.models import CourseSubmission, WebhookEvent
from api.mie.tests.factories import (
    make_approved_developer,
    make_decided_submission,
    make_rejection_reason,
    make_submission,
)
from api.platform.services import platform_settings_service
from api.users.enums import UserRole

INGEST = "/api/v1/mie/v1/submissions/"
DEV_QUEUE = "/api/v1/mie/v1/submissions/queue/"
BULK_DECISIONS = "/api/v1/admin/mie-recommendations/decisions/"


def _decide_url(submission, action) -> str:
    return f"/api/v1/mie/admin/submissions/{submission.id}/{action}/"


def _push_url(submission) -> str:
    return f"/api/v1/mie/v1/submissions/{submission.id}/course/"


@pytest.fixture
def superadmin(make_user):
    return make_user(role=UserRole.SUPER_ADMIN)


@pytest.fixture
def developer(db):
    return make_approved_developer()


@pytest.fixture
def category(db):
    return make_category(
        creator_price_with_video=Decimal("150.00"), status=CategoryStatus.ACTIVE
    )


@pytest.fixture
def version(db):
    return CourseVersion.objects.get_or_create(label="1.0")[0]


@pytest.fixture
def staged_flow(db):
    platform_settings_service.update_settings(staged_review_flow_enabled=True)


def _decide(client, actor, submission, action, body=None):
    client.force_authenticate(actor)
    response = client.post(_decide_url(submission, action), body or {}, format="json")
    client.force_authenticate(None)
    return response


def _last_event(submission, event_type) -> dict:
    event = (
        WebhookEvent.objects.filter(submission=submission, event_type=event_type)
        .order_by("-created_datetime")
        .first()
    )
    return event.payload["submission"]


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


# --- approval -----------------------------------------------------------------


@pytest.mark.django_db
def test_approval_reserves_a_course_id_and_sends_it(
    api_client, superadmin, developer, category
):
    account, _ = developer
    idea = make_submission(developer=account, title="Rust", category=category)

    response = _decide(api_client, superadmin, idea, "approve")

    assert response.status_code == 200
    idea.refresh_from_db()
    assert idea.reserved_course_id is not None
    assert _last_event(idea, WebhookEventType.SUBMISSION_APPROVED) == {
        "id": str(idea.id),
        "reference": idea.public_reference,
        "status": SubmissionStatus.APPROVED,
        "title": "Rust",
        "category_id": str(category.id),
        "course_id": str(idea.reserved_course_id),
        "category": {"id": str(category.id), "name": category.name, "status": category.status},
        "course": {"id": str(idea.reserved_course_id), "status": "RESERVED"},
    }


@pytest.mark.django_db
def test_a_pending_idea_has_no_course_id_yet(api_client, developer):
    account, key = developer

    response = api_client.post(
        INGEST, {"title": "Rust"}, format="json", HTTP_X_MIE_API_KEY=key
    )

    assert response.status_code == 201
    idea = CourseSubmission.objects.get(pk=response.data["id"])
    queued = _last_event(idea, WebhookEventType.SUBMISSION_QUEUED)
    assert queued["id"] == str(idea.id)
    assert queued["course_id"] is None
    assert idea.reserved_course_id is None


@pytest.mark.django_db
def test_rejecting_an_unapproved_idea_reserves_nothing(
    api_client, superadmin, developer
):
    account, _ = developer
    idea = make_submission(developer=account)
    make_rejection_reason(label="Prohibited subject")

    response = _decide(
        api_client,
        superadmin,
        idea,
        "reject",
        {"rejection_reason": "Prohibited subject"},
    )

    assert response.status_code == 200
    idea.refresh_from_db()
    assert idea.reserved_course_id is None
    assert _last_event(idea, WebhookEventType.SUBMISSION_REJECTED)["course_id"] is None


@pytest.mark.django_db
def test_the_course_id_survives_a_rejection_and_a_re_approval(
    api_client, superadmin, developer
):
    account, _ = developer
    idea = make_submission(developer=account)
    make_rejection_reason(label="Prohibited subject")

    _decide(api_client, superadmin, idea, "approve")
    idea.refresh_from_db()
    reserved = str(idea.reserved_course_id)
    _decide(
        api_client,
        superadmin,
        idea,
        "reject",
        {"rejection_reason": "Prohibited subject"},
    )
    _decide(api_client, superadmin, idea, "approve")

    idea.refresh_from_db()
    assert str(idea.reserved_course_id) == reserved
    assert (
        _last_event(idea, WebhookEventType.SUBMISSION_REJECTED)["course_id"] == reserved
    )
    assert (
        _last_event(idea, WebhookEventType.SUBMISSION_APPROVED)["course_id"] == reserved
    )


@pytest.mark.django_db
def test_approving_an_idea_that_already_has_a_course_reserves_that_course(
    api_client, superadmin, developer, make_user
):
    account, _ = developer
    course = make_draft_course(creator=make_user(role=UserRole.COURSE_CREATOR))
    idea = make_decided_submission(
        developer=account, approved=False, resulting_course=course
    )

    _decide(api_client, superadmin, idea, "approve")

    idea.refresh_from_db()
    assert idea.reserved_course_id == course.id
    assert _last_event(idea, WebhookEventType.SUBMISSION_APPROVED)["course_id"] == str(
        course.id
    )


@pytest.mark.django_db
def test_a_bulk_approval_reserves_one_id_per_idea(api_client, superadmin, developer):
    account, _ = developer
    ideas = [make_submission(developer=account) for _ in range(3)]

    api_client.force_authenticate(superadmin)
    response = api_client.post(
        BULK_DECISIONS,
        {"action": "approve", "ids": [str(idea.id) for idea in ideas]},
        format="json",
    )

    assert response.status_code == 200
    reserved = set()
    for idea in ideas:
        idea.refresh_from_db()
        assert idea.reserved_course_id is not None
        assert _last_event(idea, WebhookEventType.SUBMISSION_APPROVED)[
            "course_id"
        ] == str(idea.reserved_course_id)
        reserved.add(idea.reserved_course_id)
    assert len(reserved) == 3


@pytest.mark.django_db
def test_approval_needs_the_permission(api_client, developer, make_user):
    account, _ = developer
    idea = make_submission(developer=account)

    response = _decide(
        api_client, make_user(role=UserRole.COURSE_CREATOR), idea, "approve"
    )

    assert response.status_code == 403
    idea.refresh_from_db()
    assert idea.reserved_course_id is None


# --- the course push ----------------------------------------------------------


@pytest.mark.django_db
def test_the_first_push_creates_the_course_under_the_reserved_id(
    api_client, superadmin, developer, category, version, staged_flow
):
    account, key = developer
    idea = make_submission(
        developer=account, title="Build a Rust Course", category=category
    )
    _decide(api_client, superadmin, idea, "approve")
    idea.refresh_from_db()

    response = api_client.post(
        _push_url(idea),
        _text_body(title=idea.title, category=category, version=version),
        format="json",
        HTTP_X_MIE_API_KEY=key,
    )

    assert response.status_code == 201
    assert str(response.data["course_id"]) == str(idea.reserved_course_id)
    assert Course.objects.filter(pk=idea.reserved_course_id).exists()
    submitted = _last_event(idea, WebhookEventType.COURSE_SUBMITTED)
    assert submitted["course_id"] == str(idea.reserved_course_id)
    assert submitted["course"]["id"] == str(idea.reserved_course_id)
    assert submitted["id"] == str(idea.id)


# --- the developer's queue ----------------------------------------------------


@pytest.mark.django_db
def test_the_queue_shows_the_course_and_category_ids(
    api_client, superadmin, developer, category
):
    account, key = developer
    approved = make_submission(developer=account, category=category)
    pending = make_submission(developer=account)
    _decide(api_client, superadmin, approved, "approve")
    approved.refresh_from_db()

    response = api_client.get(DEV_QUEUE, HTTP_X_MIE_API_KEY=key)

    assert response.status_code == 200
    rows = {str(row["id"]): row for row in response.data["data"]["results"]}
    assert rows[str(approved.id)]["course_id"] == str(approved.reserved_course_id)
    assert rows[str(approved.id)]["category_id"] == str(category.id)
    assert rows[str(pending.id)]["course_id"] is None
    assert rows[str(pending.id)]["category_id"] is None


# --- existing ideas -----------------------------------------------------------


@pytest.mark.django_db
def test_the_migration_reserves_ids_for_ideas_approved_before_it(developer, make_user):
    account, _ = developer
    course = make_draft_course(creator=make_user(role=UserRole.COURSE_CREATOR))
    with_course = make_decided_submission(developer=account, resulting_course=course)
    approved = make_decided_submission(developer=account)
    rejected = make_decided_submission(developer=account, approved=False)
    pending = make_submission(developer=account)
    already = make_decided_submission(
        developer=account, reserved_course_id=uuid.uuid4()
    )
    kept = already.reserved_course_id
    migration = importlib.import_module(
        "api.mie.migrations.0013_coursesubmission_reserved_course_id"
    )

    migration.reserve_existing_course_ids(apps, None)

    for idea in (with_course, approved, rejected, pending, already):
        idea.refresh_from_db()
    assert with_course.reserved_course_id == course.id
    assert approved.reserved_course_id is not None
    assert rejected.reserved_course_id is None
    assert pending.reserved_course_id is None
    assert already.reserved_course_id == kept
