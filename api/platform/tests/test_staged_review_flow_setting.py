"""The staged review flow switch on /api/v1/platform-settings/.

The switch changes how every course is reviewed, so it ships off and can only
change while no course is mid-review (R4.9).
"""

import pytest

from api.courses.enums import IN_FLIGHT_COURSE_STATUSES, CourseStatus
from api.courses.tests.factories import make_draft_course
from api.platform.services import platform_settings_service
from api.users.enums import UserRole

URL = "/api/v1/platform-settings/"

# Statuses a course may sit in without blocking the switch.
RESTING_STATUSES = [CourseStatus.DRAFT, CourseStatus.PUBLISHED]


def _enable_flow_in_db(*, enabled: bool) -> None:
    row = platform_settings_service.get_settings()
    row.staged_review_flow_enabled = enabled
    row.save(update_fields=["staged_review_flow_enabled"])


def _flow_in_db() -> bool:
    # The settings row is created on first read, so a refused request that
    # never touched it still has a (default) value to compare.
    return platform_settings_service.get_settings().staged_review_flow_enabled


@pytest.mark.django_db
def test_switch_defaults_to_off_for_a_platform_that_never_set_it(
    api_client, make_user
):
    api_client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))

    response = api_client.get(URL)

    assert response.status_code == 200
    assert response.data["staged_review_flow_enabled"] is False


@pytest.mark.django_db
def test_admin_can_switch_it_on_and_back_off(api_client, make_user):
    api_client.force_authenticate(make_user(role=UserRole.ADMIN))

    on = api_client.patch(URL, {"staged_review_flow_enabled": True}, format="json")
    off = api_client.patch(URL, {"staged_review_flow_enabled": False}, format="json")

    assert on.status_code == 200
    assert on.data["staged_review_flow_enabled"] is True
    assert off.status_code == 200
    assert off.data["staged_review_flow_enabled"] is False
    assert _flow_in_db() is False


@pytest.mark.django_db
def test_a_creator_cannot_change_it(api_client, make_user):
    api_client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))

    response = api_client.patch(
        URL, {"staged_review_flow_enabled": True}, format="json"
    )

    assert response.status_code == 403
    assert _flow_in_db() is False


@pytest.mark.django_db
def test_anonymous_callers_are_refused(api_client):
    response = api_client.patch(
        URL, {"staged_review_flow_enabled": True}, format="json"
    )

    assert response.status_code == 401


@pytest.mark.django_db
def test_a_non_boolean_value_is_a_validation_error(api_client, make_user):
    api_client.force_authenticate(make_user(role=UserRole.ADMIN))

    response = api_client.patch(
        URL, {"staged_review_flow_enabled": "sometimes"}, format="json"
    )

    assert response.status_code == 400
    assert _flow_in_db() is False


@pytest.mark.django_db
@pytest.mark.parametrize("in_flight_status", sorted(IN_FLIGHT_COURSE_STATUSES))
def test_switching_on_is_refused_while_a_course_is_in_flight(
    api_client, make_user, in_flight_status
):
    make_draft_course(status=in_flight_status)
    api_client.force_authenticate(make_user(role=UserRole.ADMIN))

    response = api_client.patch(
        URL, {"staged_review_flow_enabled": True}, format="json"
    )

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "staged_review_flow_in_flight"
    assert _flow_in_db() is False


@pytest.mark.django_db
@pytest.mark.parametrize("in_flight_status", sorted(IN_FLIGHT_COURSE_STATUSES))
def test_switching_off_is_refused_while_a_course_is_in_flight(
    api_client, make_user, in_flight_status
):
    _enable_flow_in_db(enabled=True)
    make_draft_course(status=in_flight_status)
    api_client.force_authenticate(make_user(role=UserRole.ADMIN))

    response = api_client.patch(
        URL, {"staged_review_flow_enabled": False}, format="json"
    )

    assert response.status_code == 409
    assert _flow_in_db() is True


@pytest.mark.django_db
@pytest.mark.parametrize("resting_status", RESTING_STATUSES)
def test_drafts_and_published_courses_do_not_block_the_switch(
    api_client, make_user, resting_status
):
    make_draft_course(status=resting_status)
    api_client.force_authenticate(make_user(role=UserRole.ADMIN))

    response = api_client.patch(
        URL, {"staged_review_flow_enabled": True}, format="json"
    )

    assert response.status_code == 200
    assert _flow_in_db() is True


@pytest.mark.django_db
def test_resending_the_current_value_is_not_a_switch(api_client, make_user):
    """Only a real change is guarded: a settings form that resubmits every
    field must not fail just because courses are in review."""

    _enable_flow_in_db(enabled=True)
    make_draft_course(status=CourseStatus.IN_REVIEW)
    api_client.force_authenticate(make_user(role=UserRole.ADMIN))

    response = api_client.patch(
        URL, {"staged_review_flow_enabled": True}, format="json"
    )

    assert response.status_code == 200
    assert _flow_in_db() is True


@pytest.mark.django_db
def test_other_settings_are_not_blocked_by_courses_in_flight(
    api_client, make_user
):
    make_draft_course(status=CourseStatus.IN_REVIEW)
    api_client.force_authenticate(make_user(role=UserRole.ADMIN))

    response = api_client.patch(URL, {"course_module_count_min": 6}, format="json")

    assert response.status_code == 200
    assert response.data["course_module_count_min"] == 6
