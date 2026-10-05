"""Notifications page: unread count, mark all read, live pushes, and the
admin toggles that decide who is told (course_update, creator_feedback).

Pytest style, on the root conftest's `api_client` / `make_user`.
"""

from unittest import mock

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from api.courses.enums import CourseStatus
from api.courses.services import (
    course_appeal_service,
    course_service,
    course_update_alert_service,
)
from api.courses.tests.factories import build_compliant_course, make_rejected_course
from api.notification.enums import NotificationType
from api.notification.models import Notification, NotificationPreference
from api.notification.services import inapp_notification_service
from api.reviews.services import review_service
from api.users.enums import UserRole
from shared.redis.redis_service import RedisService

UNREAD_COUNT_URL = "/api/v1/users/me/notifications/unread-count/"
MARK_ALL_READ_URL = "/api/v1/users/me/notifications/mark-all-read/"
PREFERENCES_URL = "/api/v1/users/me/notification-preferences/"

pytestmark = pytest.mark.django_db


@pytest.fixture
def published():
    """Every Redis publish, as (user_id, payload) pairs."""

    with mock.patch.object(
        RedisService, "publish_user_notification", new_callable=mock.AsyncMock
    ) as publish:
        yield publish


def _notify(user, *, is_read=False, type_=NotificationType.IN_APP, title="Hi"):
    return Notification.objects.create(
        receiver=user,
        type=type_,
        title=title,
        content="Body",
        content_type="TEXT",
        is_read=is_read,
    )


def _titles_for(user):
    return list(
        Notification.objects.filter(receiver=user).values_list("title", flat=True)
    )


# --- GET /users/me/notifications/unread-count/ ---------------------------


def test_unread_count_requires_authentication(api_client):
    response = api_client.get(UNREAD_COUNT_URL)

    assert response.status_code == 401


def test_unread_count_counts_only_the_callers_unread_in_app_notifications(
    api_client, make_user
):
    user = make_user()
    other = make_user()
    _notify(user)
    _notify(user)
    _notify(user, is_read=True)
    _notify(user, type_=NotificationType.EMAIL)
    _notify(other)
    api_client.force_authenticate(user)

    response = api_client.get(UNREAD_COUNT_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == 200
    assert body["message"]
    assert body["data"] == {"unread_count": 2}


# --- POST /users/me/notifications/mark-all-read/ -------------------------


def test_mark_all_read_requires_authentication(api_client):
    response = api_client.post(MARK_ALL_READ_URL)

    assert response.status_code == 401


def test_mark_all_read_marks_only_the_callers_notifications(api_client, make_user):
    user = make_user()
    other = make_user()
    _notify(user)
    _notify(user)
    _notify(user, is_read=True)
    others = _notify(other)
    api_client.force_authenticate(user)

    response = api_client.post(MARK_ALL_READ_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {"updated": 2}
    assert api_client.get(UNREAD_COUNT_URL).json()["data"]["unread_count"] == 0
    others.refresh_from_db()
    assert others.is_read is False


def test_mark_all_read_with_nothing_unread_updates_nothing(api_client, make_user):
    user = make_user()
    _notify(user, is_read=True)
    api_client.force_authenticate(user)

    response = api_client.post(MARK_ALL_READ_URL)

    assert response.status_code == 200
    assert response.json()["data"] == {"updated": 0}


# --- Live pushes ---------------------------------------------------------


def test_emitting_pushes_each_receiver_once_after_commit(
    make_user, published, django_capture_on_commit_callbacks
):
    first, second = make_user(), make_user()

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        Notification.emit_in_app_notification(
            receivers=[first, second], title="Hi", content="Body"
        )
        assert published.await_count == 0

    for callback in callbacks:
        callback()
    assert {call.args[0] for call in published.await_args_list} == {
        first.pk,
        second.pk,
    }
    assert published.await_count == 2


def test_a_rolled_back_emit_pushes_nothing(
    make_user, published, django_capture_on_commit_callbacks
):
    user = make_user()

    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                Notification.emit_in_app_notification(
                    receivers=[user], title="Hi", content="Body"
                )
                raise RuntimeError("abort")

    assert published.await_count == 0


def test_the_pushed_list_matches_what_a_new_stream_receives_first(make_user, published):
    user = make_user()
    for index in range(inapp_notification_service.STREAM_HISTORY_SIZE + 3):
        _notify(user, title=f"In-app {index}")
    _notify(user, type_=NotificationType.EMAIL, title="Email")
    _notify(make_user(), title="Someone else's")

    inapp_notification_service.broadcast_to_users(user_ids=[user.pk])

    (user_id, payload), _ = published.await_args
    # The initial stream event is this same query, newest first, capped.
    expected = Notification._serialize_for_json(
        list(
            inapp_notification_service.get_user_notifications(user)[
                : inapp_notification_service.STREAM_HISTORY_SIZE
            ]
        )
    )
    assert user_id == user.pk
    assert payload == expected
    assert len(payload) == inapp_notification_service.STREAM_HISTORY_SIZE


def test_pushed_timestamps_match_the_list_endpoints_format(
    api_client, make_user, published
):
    user = make_user()
    _notify(user)
    api_client.force_authenticate(user)

    inapp_notification_service.broadcast_to_users(user_ids=[user.pk])

    listed = api_client.get("/api/v1/users/me/notifications/").json()
    pushed = published.await_args.args[1]
    assert (
        pushed[0]["created_datetime"]
        == listed["data"]["results"][0]["created_datetime"]
    )


def test_a_user_with_no_notifications_is_pushed_an_empty_list(make_user, published):
    user = make_user()

    inapp_notification_service.broadcast_to_users(user_ids=[user.pk])

    published.assert_awaited_once_with(user.pk, [])


def _broadcast_queries(users):
    for user in users:
        _notify(user)
    with CaptureQueriesContext(connection) as ctx:
        inapp_notification_service.broadcast_to_users(
            user_ids=[user.pk for user in users]
        )
    return len(ctx.captured_queries)


def test_broadcast_query_count_does_not_grow_with_recipients(make_user, published):
    one = _broadcast_queries([make_user()])
    five = _broadcast_queries([make_user() for _ in range(5)])

    assert one == five


def _emit_queries(users):
    with CaptureQueriesContext(connection) as ctx:
        Notification.emit_in_app_notification(
            receivers=[user.pk for user in users], title="Hi", content="Body"
        )
    return len(ctx.captured_queries)


def test_emit_query_count_does_not_grow_with_primary_key_receivers(make_user):
    one = _emit_queries([make_user()])
    five = _emit_queries([make_user() for _ in range(5)])

    assert one == five


def test_an_unknown_receiver_is_rejected(make_user):
    from rest_framework.exceptions import ValidationError

    with pytest.raises(ValidationError):
        Notification.emit_in_app_notification(
            receivers=[make_user().pk, "00000000-0000-0000-0000-000000000000"],
            title="Hi",
            content="Body",
        )


def test_mark_all_read_pushes_the_refreshed_list(
    api_client, make_user, published, django_capture_on_commit_callbacks
):
    user = make_user()
    _notify(user)
    api_client.force_authenticate(user)

    with django_capture_on_commit_callbacks(execute=True):
        api_client.post(MARK_ALL_READ_URL)

    (user_id, payload), _ = published.await_args
    assert user_id == user.pk
    assert [item["is_read"] for item in payload] == [True]


def test_toggling_one_notification_pushes_the_refreshed_list(
    api_client, make_user, published, django_capture_on_commit_callbacks
):
    user = make_user()
    notification = _notify(user)
    api_client.force_authenticate(user)

    with django_capture_on_commit_callbacks(execute=True):
        api_client.post(
            "/api/v1/users/me/notifications/toggle-read/",
            {"notification_id": str(notification.id), "read_status": True},
            format="json",
        )

    published.assert_awaited_once()
    assert published.await_args.args[1][0]["is_read"] is True


# --- Preferences ---------------------------------------------------------


def test_course_update_defaults_on_and_round_trips(api_client, make_user):
    api_client.force_authenticate(make_user(role=UserRole.ADMIN))

    assert api_client.get(PREFERENCES_URL).json()["course_update"] is True
    response = api_client.patch(
        PREFERENCES_URL, {"course_update": False}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["course_update"] is False
    assert api_client.get(PREFERENCES_URL).json()["course_update"] is False


# --- course_update -------------------------------------------------------


def _opt_out(user, **fields):
    NotificationPreference.objects.update_or_create(user=user, defaults=fields)


def test_course_update_tells_admins_except_the_actor_and_opted_out(make_user):
    actor = make_user(role=UserRole.ADMIN)
    told = make_user(role=UserRole.ADMIN)
    opted_out = make_user(role=UserRole.ADMIN)
    creator = make_user()
    _opt_out(opted_out, course_update=False)
    course = build_compliant_course(creator=creator)

    course_update_alert_service.notify_course_status_change(
        course=course, actor=actor, title="Course published", content="x"
    )

    assert _titles_for(told) == ["Course published"]
    assert _titles_for(actor) == []
    assert _titles_for(opted_out) == []
    assert _titles_for(creator) == []


def test_course_update_skips_reviewers_outside_the_admin_tier(make_user):
    actor = make_user(role=UserRole.ADMIN)
    reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
    course = build_compliant_course()

    course_update_alert_service.notify_course_status_change(
        course=course, actor=actor, title="Course published", content="x"
    )

    assert _titles_for(reviewer) == []


def test_submitting_a_course_tells_admins(make_user):
    approver = make_user(role=UserRole.ADMIN)
    opted_out = make_user(role=UserRole.ADMIN)
    _opt_out(opted_out, course_update=False)
    course = build_compliant_course()

    course_service.submit_course(course=course, actor=course.creator)

    notification = Notification.objects.get(receiver=approver)
    assert notification.title == "Course submitted"
    assert notification.metadata == {
        "course_id": str(course.id),
        "status": CourseStatus.SUBMITTED,
    }
    assert _titles_for(opted_out) == []


def test_a_qa_rejection_tells_the_other_admins(make_user):
    reviewer = make_user(role=UserRole.ADMIN)
    approver = make_user(role=UserRole.ADMIN)
    course = build_compliant_course()
    course.status = CourseStatus.QA_VERIFICATION
    course.save(update_fields=["status"])

    review_service.reject_qa(
        course=course, reviewer=reviewer, feedback={"summary": "Captions missing"}
    )

    assert _titles_for(approver) == ["Course rejected in QA"]
    assert _titles_for(reviewer) == []


# --- creator_feedback ----------------------------------------------------


def test_an_appeal_skips_deciders_who_turned_creator_feedback_off(make_user):
    told = make_user(role=UserRole.ADMIN)
    opted_out = make_user(role=UserRole.ADMIN)
    _opt_out(opted_out, creator_feedback=False)
    course = make_rejected_course()

    course_appeal_service.submit_appeal(
        user=course.creator,
        course=course,
        title="Please reconsider",
        email=course.creator.email,
        description="The flagged lesson was fixed.",
    )

    assert _titles_for(told) == ["New course-rejection appeal"]
    assert _titles_for(opted_out) == []
