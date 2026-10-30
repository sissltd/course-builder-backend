"""Mixed legacy/text-first review modes must remain independent of defaults."""

import pytest

from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.courses.tests.factories import build_compliant_course
from api.platform.services import platform_settings_service as settings
from api.reviews.enums import ReviewStage
from api.reviews.services import quality_check_service, review_service
from api.users.enums import UserRole


@pytest.mark.django_db
def test_existing_snapshot_does_not_block_default_change_or_change_course(
    api_client, make_user
):
    creator = make_user(role=UserRole.COURSE_CREATOR)
    legacy = build_compliant_course(creator=creator)
    legacy.staged_review_flow = False
    legacy.status = CourseStatus.SUBMITTED
    legacy.review_stage = ReviewStage.CONTENT
    legacy.save()
    original = Course.objects.filter(pk=legacy.pk).values().get()
    api_client.force_authenticate(make_user(role=UserRole.ADMIN))

    response = api_client.patch(
        "/api/v1/platform-settings/",
        {"staged_review_flow_enabled": True},
        format="json",
    )

    assert response.status_code == 200
    assert Course.objects.filter(pk=legacy.pk).values().get() == original
    assert not settings.uses_staged_review(legacy)
    fresh = build_compliant_course(creator=creator)
    fresh.preview_video_url = ""
    fresh.save()
    api_client.force_authenticate(creator)
    submitted = api_client.post(f"/api/v1/courses/{fresh.pk}/submit/")
    assert submitted.status_code == 200, submitted.data
    fresh.refresh_from_db()
    assert fresh.staged_review_flow is True
    # Changing the default back must not alter either submitted course.
    settings.update_settings(staged_review_flow_enabled=False)
    assert settings.uses_staged_review(fresh)
    assert not settings.uses_staged_review(legacy)
    assert settings.freeze_review_flow(course=fresh) is None


@pytest.mark.django_db
def test_failed_submit_does_not_freeze_a_mode_and_clients_cannot_set_it(
    api_client, make_user
):
    creator = make_user(role=UserRole.COURSE_CREATOR)
    course = build_compliant_course(creator=creator)
    course.preview_video_url = ""
    course.save()
    api_client.force_authenticate(creator)
    failed = api_client.post(f"/api/v1/courses/{course.pk}/submit/")
    assert failed.status_code == 400
    course.refresh_from_db()
    assert course.staged_review_flow is None
    settings.update_settings(staged_review_flow_enabled=True)
    # The submitted course receives the server default, regardless of client input.
    response = api_client.post(
        f"/api/v1/courses/{course.pk}/submit/",
        {"staged_review_flow": False},
        format="json",
    )
    assert response.status_code == 200, response.data
    course.refresh_from_db()
    assert course.staged_review_flow is True


@pytest.mark.django_db
def test_mixed_modes_keep_seat_rights_visibility_rejection_and_quality(make_user):
    creator = make_user(role=UserRole.COURSE_CREATOR)
    reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
    writer = make_user(role=UserRole.STAFF_WRITER)
    courses = []
    for staged in (False, True):
        course = build_compliant_course(creator=creator)
        course.staged_review_flow = staged
        course.status = CourseStatus.SUBMITTED
        course.review_stage = ReviewStage.CONTENT
        course.preview_video_url = ""
        course.save()
        courses.append(course)
    legacy, staged = courses
    for default in (False, True):
        settings.update_settings(staged_review_flow_enabled=default)
        assert ReviewStage.CONTENT in review_service.claimable_seats(
            user=reviewer, course=legacy
        )
        assert ReviewStage.CONTENT not in review_service.claimable_seats(
            user=writer, course=legacy
        )
        assert ReviewStage.CONTENT in review_service.claimable_seats(
            user=writer, course=staged
        )
        assert ReviewStage.CONTENT not in review_service.claimable_seats(
            user=reviewer, course=staged
        )
        visible = review_service.restrict_to_seat_role(
            Course.objects.filter(pk__in=[c.pk for c in courses]), user=reviewer
        )
        assert list(visible.values_list("pk", flat=True)) == [legacy.pk]
        assert list(
            Course.objects.filter(
                review_service.claimable_courses_q(user=writer)
            ).values_list("pk", flat=True)
        ) == [staged.pk]
        assert any(
            "preview video" in f
            for f in quality_check_service.validate_structural_standards(legacy)
        )
        assert not any(
            "preview video" in f
            for f in quality_check_service.validate_structural_standards(staged)
        )
        assert (
            review_service._return_for_revision(course=legacy, seat=ReviewStage.CONTENT)
            == "Draft"
        )
        assert (
            review_service._return_for_revision(course=staged, seat=ReviewStage.CONTENT)
            == "Needs Revision"
        )
        legacy.status = staged.status = CourseStatus.SUBMITTED
        legacy.review_stage = staged.review_stage = ReviewStage.CONTENT
    assert legacy.staged_review_flow is False
    assert staged.staged_review_flow is True


@pytest.mark.django_db
def test_http_mixed_approvals_keep_legacy_second_review_and_staged_video_handoff(
    api_client, make_user
):
    creator = make_user(role=UserRole.COURSE_CREATOR)
    legacy_reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
    writer = make_user(role=UserRole.STAFF_WRITER)
    legacy = build_compliant_course(creator=creator)
    legacy.staged_review_flow = False
    legacy.save()
    api_client.force_authenticate(creator)
    submitted = api_client.post(f"/api/v1/courses/{legacy.pk}/submit/")
    assert submitted.status_code == 200, submitted.data
    settings.update_settings(staged_review_flow_enabled=True)
    staged = build_compliant_course(creator=creator)
    staged.preview_video_url = ""
    staged.save()
    submitted = api_client.post(f"/api/v1/courses/{staged.pk}/submit/")
    assert submitted.status_code == 200, submitted.data
    settings.update_settings(staged_review_flow_enabled=False)
    for course, reviewer in ((legacy, legacy_reviewer), (staged, writer)):
        api_client.force_authenticate(reviewer)
        claim = api_client.post(f"/api/v1/review-queue/{course.pk}/claim/")
        assert claim.status_code == 200, claim.data
        approved = api_client.post(
            f"/api/v1/review-queue/{course.pk}/approve/", {}, format="json"
        )
        assert approved.status_code == 200, approved.data
        course.refresh_from_db()
    assert legacy.status == CourseStatus.SUBMITTED
    assert legacy.review_stage == ReviewStage.SECOND_REVIEW
    assert legacy.staged_review_flow is False
    assert staged.status == CourseStatus.AWAITING_VIDEO
    assert staged.staged_review_flow is True


@pytest.mark.django_db
def test_activation_command_is_permission_checked_and_audited(make_user):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from api.users.models import UserActivityLog
    from api.users.enums import UserActivityActionEnums

    admin = make_user(role=UserRole.ADMIN)
    creator = make_user(role=UserRole.COURSE_CREATOR)
    with pytest.raises(CommandError):
        call_command(
            "set_course_review_default",
            mode="text-first",
            actor_id=str(creator.id),
            reason="Owner requested staged review activation.",
        )
    assert not settings.is_staged_review_flow_enabled()
    call_command(
        "set_course_review_default",
        mode="text-first",
        actor_id=str(admin.id),
        reason="Owner requested staged review activation after snapshot migration.",
    )
    assert settings.is_staged_review_flow_enabled()
    event = UserActivityLog.objects.get(
        action=UserActivityActionEnums.PLATFORM_REVIEW_DEFAULT_UPDATED
    )
    assert event.actor_user_id == admin.id
    assert event.details["previous"] is False
    assert event.details["current"] is True
    assert event.details["existing_courses_unchanged"] is True
