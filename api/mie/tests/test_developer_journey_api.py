"""Each developer account's journey stage: the furthest any of its ideas or
courses has reached, shown to admins (list, detail, filter) and to the
developer on /me.

Every request goes to the literal URL its caller uses.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from api.courses.enums import CourseStatus
from api.courses.tests.factories import make_draft_course
from api.mie.enums import DeveloperAccountStatus, SubmissionStatus
from api.mie.tests.factories import (
    make_approved_developer,
    make_decided_submission,
    make_developer_account,
    make_submission,
)
from api.users.enums import UserRole

DEVELOPERS = "/api/v1/mie/admin/developers/"
ME = "/api/v1/mie/v1/me/"


@pytest.fixture
def admin_client(api_client, make_user):
    api_client.force_authenticate(make_user(role=UserRole.SUPER_ADMIN))
    return api_client


def _with_course(developer, course_status):
    """An approved idea of `developer` whose course sits at `course_status`."""

    course = make_draft_course(status=course_status)
    return make_decided_submission(developer=developer, resulting_course=course)


def _journey(client, developer):
    response = client.get(f"{DEVELOPERS}{developer.id}/")
    assert response.status_code == 200
    return response.data["journey"]


def _pending(_developer):
    pass


@pytest.mark.parametrize(
    ("account_status", "build", "stage", "step"),
    [
        (DeveloperAccountStatus.PENDING, _pending, "REGISTERED", 1),
        (DeveloperAccountStatus.APPROVED, _pending, "ONBOARDED", 2),
        (DeveloperAccountStatus.SUSPENDED, _pending, "ONBOARDED", 2),
        (
            DeveloperAccountStatus.APPROVED,
            lambda dev: make_submission(developer=dev),
            "IDEA_SUBMITTED",
            3,
        ),
        (
            DeveloperAccountStatus.APPROVED,
            lambda dev: make_decided_submission(developer=dev),
            "IDEA_APPROVED",
            4,
        ),
        (
            DeveloperAccountStatus.APPROVED,
            lambda dev: _with_course(dev, CourseStatus.IN_REVIEW),
            "COURSE_DELIVERED",
            5,
        ),
        (
            DeveloperAccountStatus.APPROVED,
            lambda dev: _with_course(dev, CourseStatus.ARCHIVED),
            "COURSE_DELIVERED",
            5,
        ),
        (
            DeveloperAccountStatus.APPROVED,
            lambda dev: _with_course(dev, CourseStatus.AWAITING_VIDEO),
            "TEXT_APPROVED",
            6,
        ),
        (
            DeveloperAccountStatus.APPROVED,
            lambda dev: _with_course(dev, CourseStatus.QA_VERIFICATION),
            "TEXT_APPROVED",
            6,
        ),
        (
            DeveloperAccountStatus.APPROVED,
            lambda dev: _with_course(dev, CourseStatus.APPROVED),
            "QA_PASSED",
            7,
        ),
        (
            DeveloperAccountStatus.APPROVED,
            lambda dev: _with_course(dev, CourseStatus.PUBLISHED),
            "PUBLISHED",
            8,
        ),
    ],
)
def test_the_stage_is_worked_out_from_ideas_and_courses(
    admin_client, account_status, build, stage, step
):
    developer = make_developer_account(status=account_status)
    build(developer)

    journey = _journey(admin_client, developer)

    assert journey["stage"] == stage
    assert journey["step"] == step
    assert journey["total_steps"] == 8
    assert journey["label"]
    assert journey["next_step"]


def test_the_furthest_idea_or_course_sets_the_stage_and_counts(admin_client):
    developer, _ = make_approved_developer()
    make_submission(developer=developer)
    make_decided_submission(developer=developer, approved=False)
    _with_course(developer, CourseStatus.IN_REVIEW)
    _with_course(developer, CourseStatus.PUBLISHED)

    journey = _journey(admin_client, developer)

    assert journey["stage"] == "PUBLISHED"
    assert {
        key: journey[key]
        for key in (
            "ideas_submitted",
            "ideas_approved",
            "courses_delivered",
            "courses_published",
        )
    } == {
        "ideas_submitted": 4,
        "ideas_approved": 2,
        "courses_delivered": 2,
        "courses_published": 1,
    }


def test_the_stage_moves_back_with_its_course(admin_client):
    developer, _ = make_approved_developer()
    submission = _with_course(developer, CourseStatus.AWAITING_VIDEO)
    assert _journey(admin_client, developer)["stage"] == "TEXT_APPROVED"

    course = submission.resulting_course
    course.status = CourseStatus.NEEDS_REVISION
    course.save(update_fields=["status"])

    assert _journey(admin_client, developer)["stage"] == "COURSE_DELIVERED"


def test_a_reversed_idea_approval_drops_back_to_submitted(admin_client):
    developer, _ = make_approved_developer()
    submission = make_decided_submission(developer=developer)
    submission.status = SubmissionStatus.REJECTED
    submission.save(update_fields=["status"])

    assert _journey(admin_client, developer)["stage"] == "IDEA_SUBMITTED"


def test_the_admin_list_filters_on_the_stage(admin_client):
    published, _ = make_approved_developer()
    _with_course(published, CourseStatus.PUBLISHED)
    onboarded, _ = make_approved_developer()
    make_developer_account()

    response = admin_client.get(f"{DEVELOPERS}?journey_stage=PUBLISHED")

    assert response.status_code == 200
    rows = response.data["data"]["results"]
    assert [row["id"] for row in rows] == [str(published.id)]
    assert rows[0]["journey"]["courses_published"] == 1

    response = admin_client.get(f"{DEVELOPERS}?journey_stage=ONBOARDED&status=APPROVED")
    assert [row["id"] for row in response.data["data"]["results"]] == [str(onboarded.id)]


def test_an_unknown_stage_filter_is_a_400(admin_client):
    response = admin_client.get(f"{DEVELOPERS}?journey_stage=SHIPPED")

    assert response.status_code == 400


def test_the_list_costs_the_same_however_many_developers(admin_client):
    def queries():
        with CaptureQueriesContext(connection) as captured:
            response = admin_client.get(DEVELOPERS)
        assert response.status_code == 200
        return len(captured)

    developer, _ = make_approved_developer()
    _with_course(developer, CourseStatus.IN_REVIEW)
    one = queries()
    for course_status in (CourseStatus.PUBLISHED, CourseStatus.AWAITING_VIDEO):
        other, _ = make_approved_developer()
        _with_course(other, course_status)
        make_submission(developer=other)

    assert queries() == one


def test_approving_a_developer_returns_the_stage_after_approval(admin_client):
    developer = make_developer_account()

    response = admin_client.post(f"{DEVELOPERS}{developer.id}/approve/")

    assert response.status_code == 200
    assert response.data["account"]["journey"]["stage"] == "ONBOARDED"


def test_the_developer_sees_their_own_journey_on_me(db, api_client):
    developer, key = make_approved_developer()
    make_decided_submission(developer=developer)
    someone_else, _ = make_approved_developer()
    _with_course(someone_else, CourseStatus.PUBLISHED)

    response = api_client.get(ME, HTTP_X_MIE_API_KEY=key)

    assert response.status_code == 200
    journey = response.data["journey"]
    assert journey["stage"] == "IDEA_APPROVED"
    assert journey["ideas_approved"] == 1
    assert journey["courses_published"] == 0
    assert "/mie/v1/submissions/{id}/course/" in journey["next_step"]


def test_the_developer_docs_carry_the_journey(db, api_client):
    developer, key = make_approved_developer()

    response = api_client.get("/api/v1/mie/v1/documentation/", HTTP_X_MIE_API_KEY=key)

    assert response.status_code == 200
    account = response.data["your_account"]
    assert account["journey"]["stage"] == "ONBOARDED"
    assert [row["stage"] for row in account["journey_stages"]] == [
        "REGISTERED",
        "ONBOARDED",
        "IDEA_SUBMITTED",
        "IDEA_APPROVED",
        "COURSE_DELIVERED",
        "TEXT_APPROVED",
        "QA_PASSED",
        "PUBLISHED",
    ]
