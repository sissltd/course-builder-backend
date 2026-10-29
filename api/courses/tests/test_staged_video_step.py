"""Text-first submission and the video step of the staged review flow.

With PlatformSettings.staged_review_flow_enabled on, a course is submitted as
text only, reviewed, and gets its video afterwards through
`POST /api/v1/courses/{id}/video-decision/` and `.../submit-video/`. With the
switch off the original rules (preview video required at submission) hold.
"""

import pytest

from api.courses.enums import (
    CourseSourceType,
    CourseStatus,
    LessonContentType,
    VideoProvider,
)
from api.courses.models import Course, Lesson, LessonContentBlock
from api.courses.tests.factories import build_compliant_course
from api.platform.services import platform_settings_service
from api.reviews.enums import MediaAssetKind, ReviewStage
from api.reviews.models import MediaAsset, ReviewAssignment
from api.users.enums import UserRole

PREVIEW_URL = "https://example.com/preview.mp4"


@pytest.fixture
def staged_flow(db):
    platform_settings_service.update_settings(staged_review_flow_enabled=True)


@pytest.fixture
def creator(make_user):
    return make_user(role=UserRole.COURSE_CREATOR)


def _text_only_course(creator, **fields):
    """A course that passes every text rule and carries no video."""

    course = build_compliant_course(creator=creator)
    fields.setdefault("preview_video_url", "")
    for name, value in fields.items():
        setattr(course, name, value)
    course.save()
    return course


def _awaiting_video(creator, **fields):
    fields.setdefault("status", CourseStatus.AWAITING_VIDEO)
    fields.setdefault("video_provider", VideoProvider.CREATOR)
    fields.setdefault("preview_video_url", PREVIEW_URL)
    return _text_only_course(creator, **fields)


def _messages(response) -> str:
    return " ".join(error["message"] for error in response.json()["errors"])


# --- Submission rules: the switch decides which video rule applies ----------


@pytest.mark.django_db
def test_off_a_preview_video_is_still_required_at_submission(api_client, creator):
    course = _text_only_course(creator)
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit/")

    assert response.status_code == 400
    assert "preview video" in _messages(response)
    course.refresh_from_db()
    assert course.status == CourseStatus.DRAFT


@pytest.mark.django_db
def test_off_a_course_with_a_preview_video_submits_as_before(api_client, creator):
    course = _text_only_course(creator, preview_video_url=PREVIEW_URL)
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit/")

    assert response.status_code == 200
    assert response.data["status"] == CourseStatus.SUBMITTED


@pytest.mark.django_db
def test_on_a_text_only_course_submits_to_the_first_seat(
    api_client, creator, staged_flow
):
    course = _text_only_course(creator)
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit/")

    assert response.status_code == 200
    assert response.data["status"] == CourseStatus.SUBMITTED
    assert response.data["review_stage"] == ReviewStage.CONTENT
    assert response.data["video_attached_at"] is None


@pytest.mark.django_db
def test_on_a_course_with_a_preview_video_is_refused(
    api_client, creator, staged_flow
):
    course = _text_only_course(creator, preview_video_url=PREVIEW_URL)
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit/")

    assert response.status_code == 400
    assert "Remove the preview video" in _messages(response)
    course.refresh_from_db()
    assert course.status == CourseStatus.DRAFT


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["video_url", "embedded_link"])
def test_on_a_lesson_with_a_video_reference_is_refused(
    api_client, creator, staged_flow, field
):
    course = _text_only_course(creator)
    lesson = Lesson.objects.filter(module__course=course).first()
    setattr(lesson, field, "https://example.com/lesson.mp4")
    lesson.save()
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit/")

    assert response.status_code == 400
    assert f"Lesson '{lesson.title}' must not have a video yet" in _messages(response)


@pytest.mark.django_db
def test_on_a_video_content_block_is_refused(api_client, creator, staged_flow):
    course = _text_only_course(creator)
    lesson = Lesson.objects.filter(module__course=course).first()
    LessonContentBlock.objects.create(
        lesson=lesson,
        order=1,
        block_type=LessonContentBlock.BlockType.VIDEO,
        media_url="https://example.com/block.mp4",
    )
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit/")

    assert response.status_code == 400
    assert "video blocks" in _messages(response)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kind, allowed",
    [
        (MediaAssetKind.VIDEO, False),
        (MediaAssetKind.PREVIEW_VIDEO, False),
        (MediaAssetKind.THUMBNAIL, True),
    ],
)
def test_on_only_video_media_assets_block_a_text_only_submission(
    api_client, creator, staged_flow, kind, allowed
):
    course = _text_only_course(creator)
    MediaAsset.objects.create(course=course, kind=kind, url="https://example.com/a")
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit/")

    assert (response.status_code == 200) is allowed


@pytest.mark.django_db
def test_on_a_video_lesson_may_exist_as_a_script_before_its_video(
    api_client, creator, staged_flow
):
    course = _text_only_course(creator)
    Lesson.objects.filter(module__course=course).update(
        content_type=LessonContentType.VIDEO
    )
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit/")

    assert response.status_code == 200


# --- Lesson writes ----------------------------------------------------------

LESSON_BODY = {
    "title": "A video lesson",
    "order": 99,
    "lesson_type": "VIDEO",
    "script": "Words for the narrator.",
    "learning_objectives": ["a", "b"],
    "duration_minutes": 10,
}


@pytest.mark.django_db
def test_off_a_video_lesson_without_media_is_rejected(api_client, creator):
    course = _text_only_course(creator)
    module = course.modules.first()
    api_client.force_authenticate(creator)

    response = api_client.post(
        f"/api/v1/courses/{course.id}/modules/{module.id}/lessons/",
        LESSON_BODY,
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_on_a_video_lesson_without_media_is_accepted(api_client, creator, staged_flow):
    course = _text_only_course(creator)
    module = course.modules.first()
    api_client.force_authenticate(creator)

    response = api_client.post(
        f"/api/v1/courses/{course.id}/modules/{module.id}/lessons/",
        LESSON_BODY,
        format="json",
    )

    assert response.status_code == 201


# --- POST /courses/{id}/video-decision/ -------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "will_provide, expected",
    [(True, VideoProvider.CREATOR), (False, VideoProvider.PRODUCTION_ENGINE)],
)
def test_the_creator_chooses_who_supplies_the_video(
    api_client, creator, will_provide, expected
):
    course = _awaiting_video(creator, video_provider="")
    api_client.force_authenticate(creator)

    response = api_client.post(
        f"/api/v1/courses/{course.id}/video-decision/",
        {"will_provide": will_provide},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["video_provider"] == expected
    assert response.data["status"] == CourseStatus.AWAITING_VIDEO
    course.refresh_from_db()
    assert course.video_provider == expected


@pytest.mark.django_db
def test_the_choice_can_be_changed_until_the_video_is_submitted(api_client, creator):
    course = _awaiting_video(creator, video_provider=VideoProvider.PRODUCTION_ENGINE)
    api_client.force_authenticate(creator)

    response = api_client.post(
        f"/api/v1/courses/{course.id}/video-decision/",
        {"will_provide": True},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["video_provider"] == VideoProvider.CREATOR


@pytest.mark.django_db
def test_a_missing_choice_is_a_validation_error(api_client, creator):
    course = _awaiting_video(creator, video_provider="")
    api_client.force_authenticate(creator)

    response = api_client.post(
        f"/api/v1/courses/{course.id}/video-decision/", {}, format="json"
    )

    assert response.status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status", sorted(set(CourseStatus) - {CourseStatus.AWAITING_VIDEO})
)
def test_deciding_is_a_conflict_unless_the_course_awaits_video(
    api_client, creator, status
):
    course = _awaiting_video(creator, status=status)
    api_client.force_authenticate(creator)

    response = api_client.post(
        f"/api/v1/courses/{course.id}/video-decision/",
        {"will_provide": True},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "course_not_awaiting_video"


@pytest.mark.django_db
def test_a_developer_course_cannot_be_decided_by_its_creator_account(
    api_client, creator
):
    course = _awaiting_video(
        creator,
        source_type=CourseSourceType.DEVELOPER_API,
        video_provider=VideoProvider.DEVELOPER,
    )
    api_client.force_authenticate(creator)

    response = api_client.post(
        f"/api/v1/courses/{course.id}/video-decision/",
        {"will_provide": False},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "video_provider_conflict"
    course.refresh_from_db()
    assert course.video_provider == VideoProvider.DEVELOPER


@pytest.mark.django_db
def test_another_creators_course_is_a_404_not_a_403(api_client, creator, make_user):
    course = _awaiting_video(creator)
    api_client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))

    response = api_client.post(
        f"/api/v1/courses/{course.id}/video-decision/",
        {"will_provide": True},
        format="json",
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_deciding_needs_a_signed_in_user_with_course_permissions(
    api_client, creator, make_user
):
    course = _awaiting_video(creator)
    url = f"/api/v1/courses/{course.id}/video-decision/"

    anonymous = api_client.post(url, {"will_provide": True}, format="json")
    api_client.force_authenticate(make_user(role=UserRole.QA_REVIEWER))
    unauthorised = api_client.post(url, {"will_provide": True}, format="json")

    assert anonymous.status_code == 401
    assert unauthorised.status_code == 403


# --- POST /courses/{id}/submit-video/ ---------------------------------------


@pytest.mark.django_db
def test_submitting_the_video_sends_the_course_to_the_second_seat(
    api_client, creator, staged_flow
):
    course = _awaiting_video(creator)
    first_seat = ReviewAssignment.objects.create(
        course=course, stage=ReviewStage.CONTENT, completed_at=course.created_datetime
    )
    stale_second_seat = ReviewAssignment.objects.create(
        course=course,
        stage=ReviewStage.SECOND_REVIEW,
        reviewer=creator,
        claimed_at=course.created_datetime,
        completed_at=course.created_datetime,
    )
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit-video/")

    assert response.status_code == 200
    assert response.data["status"] == CourseStatus.SUBMITTED
    assert response.data["review_stage"] == ReviewStage.SECOND_REVIEW
    assert response.data["video_attached_at"] is not None
    first_seat.refresh_from_db()
    stale_second_seat.refresh_from_db()
    assert first_seat.completed_at is not None  # text approval is kept
    assert stale_second_seat.completed_at is None
    assert stale_second_seat.reviewer_id is None


@pytest.mark.django_db
def test_a_course_with_its_video_passes_the_check_reviewers_see(
    api_client, creator, staged_flow
):
    from api.reviews.services import quality_check_service

    course = _awaiting_video(creator)
    api_client.force_authenticate(creator)
    api_client.post(f"/api/v1/courses/{course.id}/submit-video/")
    course.refresh_from_db()

    assert quality_check_service.validate_structural_standards(course) == []


@pytest.mark.django_db
def test_the_preview_video_is_required_to_submit_the_video(
    api_client, creator, staged_flow
):
    course = _awaiting_video(creator, preview_video_url="")
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit-video/")

    assert response.status_code == 400
    assert response.json()["errors"][0]["field_name"] == "structural_standards"
    assert "preview video" in _messages(response)
    course.refresh_from_db()
    assert course.status == CourseStatus.AWAITING_VIDEO
    assert course.video_attached_at is None


@pytest.mark.django_db
def test_every_video_lesson_needs_a_media_reference(api_client, creator, staged_flow):
    course = _awaiting_video(creator)
    lesson = Lesson.objects.filter(module__course=course).first()
    lesson.content_type = LessonContentType.VIDEO
    lesson.save()
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit-video/")

    assert response.status_code == 400
    assert f"Video lesson '{lesson.title}'" in _messages(response)


@pytest.mark.django_db
def test_text_rules_are_checked_again_with_the_video(api_client, creator, staged_flow):
    course = _awaiting_video(creator, description="too short")
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit-video/")

    assert response.status_code == 400
    assert "description" in _messages(response)


@pytest.mark.django_db
def test_a_developers_linked_account_can_submit_the_developers_video(
    api_client, creator, staged_flow
):
    course = _awaiting_video(
        creator,
        source_type=CourseSourceType.DEVELOPER_API,
        video_provider=VideoProvider.DEVELOPER,
    )
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit-video/")

    assert response.status_code == 200
    assert response.data["status"] == CourseStatus.SUBMITTED


@pytest.mark.django_db
@pytest.mark.parametrize("provider", ["", VideoProvider.PRODUCTION_ENGINE])
def test_only_the_party_that_supplies_the_video_may_submit_it(
    api_client, creator, staged_flow, provider
):
    course = _awaiting_video(creator, video_provider=provider)
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit-video/")

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "video_provider_conflict"
    course.refresh_from_db()
    assert course.status == CourseStatus.AWAITING_VIDEO


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status", sorted(set(CourseStatus) - {CourseStatus.AWAITING_VIDEO})
)
def test_submitting_video_is_a_conflict_unless_the_course_awaits_it(
    api_client, creator, staged_flow, status
):
    course = _awaiting_video(creator, status=status)
    api_client.force_authenticate(creator)

    response = api_client.post(f"/api/v1/courses/{course.id}/submit-video/")

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "course_not_awaiting_video"
    assert Course.objects.get(pk=course.pk).status == status


@pytest.mark.django_db
def test_submitting_video_for_another_creators_course_is_a_404(
    api_client, creator, make_user, staged_flow
):
    course = _awaiting_video(creator)
    api_client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))

    response = api_client.post(f"/api/v1/courses/{course.id}/submit-video/")

    assert response.status_code == 404
    assert Course.objects.get(pk=course.pk).status == CourseStatus.AWAITING_VIDEO


@pytest.mark.django_db
def test_submitting_video_needs_a_signed_in_user_with_course_permissions(
    api_client, creator, make_user, staged_flow
):
    course = _awaiting_video(creator)
    url = f"/api/v1/courses/{course.id}/submit-video/"

    anonymous = api_client.post(url)
    api_client.force_authenticate(make_user(role=UserRole.QA_REVIEWER))
    unauthorised = api_client.post(url)

    assert anonymous.status_code == 401
    assert unauthorised.status_code == 403
