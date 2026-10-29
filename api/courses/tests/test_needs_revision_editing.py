"""A course is editable while Draft or Needs Revision, and no later.

NEEDS_REVISION only exists with the staged review flow on, but the builder's
edit gate reads Course.is_editable regardless, so these tests set the status
directly. Deleting stays Draft-only.
"""

import pytest

from api.courses.enums import EDITABLE_COURSE_STATUSES, CourseStatus
from api.courses.models import Lesson, Module
from api.courses.tests.factories import make_category, make_draft_course
from api.users.enums import UserRole

EDITABLE = sorted(EDITABLE_COURSE_STATUSES)
LOCKED = sorted(set(CourseStatus) - EDITABLE_COURSE_STATUSES)

MODULE_BODY = {
    "title": "Module 1",
    "order": 1,
    "description": "An introduction.",
    "learning_objectives": ["Install the tools", "Create a project"],
}
LESSON_BODY = {
    "title": "L1",
    "order": 1,
    "script": "x",
    "learning_objectives": ["a", "b"],
    "duration_minutes": 10,
}


@pytest.fixture
def creator(make_user):
    return make_user(role=UserRole.COURSE_CREATOR)


def _course(creator, status):
    return make_draft_course(
        creator=creator, category=make_category(), status=status
    )


@pytest.mark.django_db
def test_editable_statuses_are_exactly_draft_and_needs_revision():
    assert set(EDITABLE_COURSE_STATUSES) == {
        CourseStatus.DRAFT,
        CourseStatus.NEEDS_REVISION,
    }


@pytest.mark.django_db
@pytest.mark.parametrize("status", EDITABLE)
def test_the_owner_can_add_a_module_while_editable(api_client, creator, status):
    course = _course(creator, status)
    api_client.force_authenticate(creator)

    response = api_client.post(
        f"/api/v1/courses/{course.id}/modules/", MODULE_BODY, format="json"
    )

    assert response.status_code == 201
    assert Module.objects.filter(course=course).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("status", LOCKED)
def test_the_owner_cannot_add_a_module_once_locked(api_client, creator, status):
    course = _course(creator, status)
    api_client.force_authenticate(creator)

    response = api_client.post(
        f"/api/v1/courses/{course.id}/modules/", MODULE_BODY, format="json"
    )

    assert response.status_code == 400
    assert not Module.objects.filter(course=course).exists()


@pytest.mark.django_db
def test_a_needs_revision_course_can_have_a_module_renamed_and_removed(
    api_client, creator
):
    course = _course(creator, CourseStatus.NEEDS_REVISION)
    module = Module.objects.create(course=course, title="Old", order=1)
    api_client.force_authenticate(creator)
    url = f"/api/v1/courses/{course.id}/modules/{module.id}/"

    renamed = api_client.patch(url, {"title": "New"}, format="json")
    removed = api_client.delete(url)

    assert renamed.status_code == 200
    assert renamed.data["title"] == "New"
    assert removed.status_code == 204
    assert not Module.objects.filter(pk=module.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("status", EDITABLE)
def test_lessons_follow_the_same_gate(api_client, creator, status):
    course = _course(creator, status)
    module = Module.objects.create(course=course, title="M1", order=1)
    api_client.force_authenticate(creator)

    response = api_client.post(
        f"/api/v1/courses/{course.id}/modules/{module.id}/lessons/",
        LESSON_BODY,
        format="json",
    )

    assert response.status_code == 201
    assert Lesson.objects.filter(module=module).count() == 1


@pytest.mark.django_db
def test_a_submitted_course_still_refuses_lesson_edits(api_client, creator):
    course = _course(creator, CourseStatus.SUBMITTED)
    module = Module.objects.create(course=course, title="M1", order=1)
    api_client.force_authenticate(creator)

    response = api_client.post(
        f"/api/v1/courses/{course.id}/modules/{module.id}/lessons/",
        LESSON_BODY,
        format="json",
    )

    assert response.status_code == 400
    assert not Lesson.objects.filter(module=module).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("status", EDITABLE)
def test_course_details_can_be_patched_while_editable(api_client, creator, status):
    course = _course(creator, status)
    api_client.force_authenticate(creator)

    response = api_client.patch(
        f"/api/v1/courses/{course.id}/", {"title": "Renamed"}, format="json"
    )

    assert response.status_code == 200
    assert response.data["title"] == "Renamed"


@pytest.mark.django_db
def test_course_details_cannot_be_patched_once_in_review(api_client, creator):
    course = _course(creator, CourseStatus.IN_REVIEW)
    api_client.force_authenticate(creator)

    response = api_client.patch(
        f"/api/v1/courses/{course.id}/", {"title": "Renamed"}, format="json"
    )

    assert response.status_code == 400
    course.refresh_from_db()
    assert course.title == "Test Course"


@pytest.mark.django_db
def test_a_needs_revision_course_cannot_be_deleted(api_client, creator):
    """Only a Draft may be deleted: a course that has been through review
    carries review history the creator must not be able to discard."""

    course = _course(creator, CourseStatus.NEEDS_REVISION)
    api_client.force_authenticate(creator)

    response = api_client.delete(f"/api/v1/courses/{course.id}/")

    assert response.status_code == 400


@pytest.mark.django_db
def test_ai_thumbnail_lookup_includes_needs_revision_but_not_locked(
    api_client, creator
):
    """An empty body is a 400 once the course is found and a 404 when it is
    not, so the two outcomes tell the lookup apart without calling a
    provider."""

    editable = _course(creator, CourseStatus.NEEDS_REVISION)
    locked = _course(creator, CourseStatus.SUBMITTED)
    api_client.force_authenticate(creator)

    found = api_client.post(
        f"/api/v1/courses/{editable.id}/ai-thumbnail/", {}, format="json"
    )
    missing = api_client.post(
        f"/api/v1/courses/{locked.id}/ai-thumbnail/", {}, format="json"
    )

    assert found.status_code == 400
    assert missing.status_code == 404
