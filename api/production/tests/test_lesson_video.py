"""Lesson video, end to end: a course waiting on engine video is voiced,
drawn, rendered, checked and delivered to the second review seat; a reviewer's
media flags send just those lessons back through the engine, and the course
returns to the seat that asked. Real ffmpeg, fake vendors (conftest).
"""

from decimal import Decimal
from unittest import mock

import pytest

from api.courses.enums import CourseStatus, VideoProvider
from api.courses.models import Course, Lesson
from api.courses.tests.factories import build_compliant_course
from api.notification.models import Notification
from api.operations.enums import CostCategory, PipelineJobStatus
from api.operations.models import PipelineJob, ProductionCost
from api.production.enums import ProductionRunStatus, ReworkAction
from api.production.models import ProductionRun, PronunciationEntry
from api.production.services import production_service
from api.production.tasks import run_production
from api.production.tests.conftest import BUCKET, set_settings
from api.reviews.enums import MediaAssetKind, ReviewStage
from api.reviews.models import MediaAsset
from api.reviews.services import quality_review_service
from api.users.enums import UserActivityActionEnums, UserRole
from api.users.models import UserActivityLog

QUEUE = "/api/v1/review-queue/"
RUNS = "/api/v1/admin/production/runs/"


@pytest.fixture
def engine(db):
    set_settings(
        production_enabled=True,
        staged_review_flow_enabled=True,
        course_module_count_min=1,
        course_lessons_per_module_min=1,
    )


@pytest.fixture
def creator(make_user):
    return make_user(role=UserRole.COURSE_CREATOR)


@pytest.fixture
def media(engine, store, voices, hearing, storyboard, eyes, no_dispatch):
    return {
        "store": store, "primary": voices[0], "fallback": voices[1], "hearing": hearing,
        "storyboard": storyboard, "eyes": eyes,
    }


def _awaiting_engine_video(creator, scripts=None) -> Course:
    """Two lessons, text approved, video left to the engine."""

    course = build_compliant_course(creator=creator, module_count=1, lessons_per_module=2)
    lessons = list(Lesson.objects.filter(module__course=course).order_by("order"))
    for index, lesson in enumerate(lessons):
        lesson.script = (scripts or {}).get(index) or f"{lesson.title} explained. " + "word " * 600 + "Done."
    Lesson.objects.bulk_update(lessons, ["script"])
    course.status = CourseStatus.AWAITING_VIDEO
    course.video_provider = VideoProvider.PRODUCTION_ENGINE
    course.preview_video_url = ""
    course.duration_estimate_minutes = 40
    course.save()
    return course


def _produce(course, actor):
    run = production_service.request_production(course=course, actor=actor)
    run_production(str(run.id))
    run.refresh_from_db()
    return run


def _post(client, user, path, body=None):
    client.force_authenticate(user)
    return client.post(path, body or {}, format="json")


# --- first production --------------------------------------------------------------


@pytest.mark.django_db
def test_a_run_makes_checked_video_and_sends_the_course_to_the_second_seat(creator, media):
    course = _awaiting_engine_video(creator)

    run = _produce(course, creator)

    assert run.status == ProductionRunStatus.COMPLETED, run.status_reason
    course.refresh_from_db()
    assert course.status == CourseStatus.SUBMITTED
    assert course.review_stage == ReviewStage.SECOND_REVIEW
    assert course.video_attached_at is not None
    assert course.preview_video_url.startswith(BUCKET)

    lessons = list(Lesson.objects.filter(module__course=course))
    videos = {asset.lesson_id: asset for asset in MediaAsset.objects.filter(course=course, kind=MediaAssetKind.VIDEO)}
    assert set(videos) == {lesson.id for lesson in lessons}
    for lesson in lessons:
        evidence = videos[lesson.id]
        assert lesson.video_url == evidence.url
        assert evidence.resolution == "1920x1080"
        assert evidence.caption_accuracy_percent == Decimal("100.00")
        assert abs(evidence.audio_lufs - Decimal("-16")) <= 1
        assert evidence.audio_video_drift_ms <= 100
        srt = media["store"].read_text(lesson.video_script_file)
        assert "-->" in srt and lesson.title in srt  # captions are the approved words
    trailer = MediaAsset.objects.get(course=course, kind=MediaAssetKind.PREVIEW_VIDEO)
    assert 60 <= trailer.duration_seconds <= 120
    assert MediaAsset.objects.filter(course=course, kind=MediaAssetKind.THUMBNAIL).exists()
    # Everything the QA seat checks is present.
    assert quality_review_service.required_media_failures(course=course) == []

    steps = set(PipelineJob.objects.filter(run=run, lesson__isnull=False).values_list("step", flat=True))
    assert steps == {"storyboard", "narration", "visuals", "render", "quality_check"}
    assert ProductionCost.objects.filter(course=course, category=CostCategory.VOICE).exists()
    # One narration per lesson plus the trailer, all by the primary voice.
    assert len(media["primary"].calls) == len(lessons) + 1
    assert media["fallback"].calls == []
    assert Notification.objects.filter(receiver=creator, title="Video ready").exists()
    engine_user = production_service.engine_user()
    assert not engine_user.is_active
    assert UserActivityLog.objects.filter(user=engine_user, action=UserActivityActionEnums.COURSE_VIDEO_SUBMITTED).exists()


@pytest.mark.django_db
def test_a_lesson_failing_the_quality_check_is_retaken_with_the_next_voice_then_fails_the_run(creator, media, make_user):
    admin = make_user(role=UserRole.ADMIN)
    course = _awaiting_engine_video(creator)
    media["hearing"].garbled = True

    run = _produce(course, creator)

    assert run.status == ProductionRunStatus.FAILED
    assert "caption accuracy" in run.status_reason
    assert len(media["primary"].calls) == 1 and len(media["fallback"].calls) == 1
    assert PipelineJob.objects.filter(run=run, step="quality_check", status=PipelineJobStatus.FAILED).count() == 2
    course.refresh_from_db()
    assert course.status == CourseStatus.AWAITING_VIDEO
    assert not MediaAsset.objects.filter(course=course).exists()  # nothing unchecked was delivered
    assert Notification.objects.filter(receiver=admin, title="Production failed").exists()


@pytest.mark.django_db
def test_when_the_primary_voice_refuses_the_fallback_voices_the_lesson(creator, media):
    course = _awaiting_engine_video(creator)
    media["primary"].failing = "Lesson 0-1"

    run = _produce(course, creator)

    assert run.status == ProductionRunStatus.COMPLETED, run.status_reason
    second = Lesson.objects.get(module__course=course, order=1)
    evidence = MediaAsset.objects.get(lesson=second, kind=MediaAssetKind.VIDEO)
    assert evidence.verification["voice"] == "Google TTS"
    assert len(media["fallback"].calls) == 1


@pytest.mark.django_db
def test_a_retried_run_reuses_the_lessons_it_already_made(creator, media, make_user, api_client):
    admin = make_user(role=UserRole.ADMIN)
    course = _awaiting_engine_video(creator)
    media["primary"].failing = media["fallback"].failing = "Lesson 0-1"

    failed = _produce(course, creator)
    assert failed.status == ProductionRunStatus.FAILED
    first_lesson_calls = [text for text in media["primary"].calls if "Lesson 0-0" in text]
    assert len(first_lesson_calls) == 1

    media["primary"].failing = media["fallback"].failing = None
    response = _post(api_client, admin, f"{RUNS}{failed.id}/retry/")
    assert response.status_code == 200
    run_production(str(failed.id))

    failed.refresh_from_db()
    assert failed.status == ProductionRunStatus.COMPLETED, failed.status_reason
    assert [text for text in media["primary"].calls if "Lesson 0-0" in text] == first_lesson_calls


@pytest.mark.django_db
def test_a_script_edited_while_the_video_is_made_sends_the_run_round_again(creator, media):
    course = _awaiting_engine_video(creator)
    real_trailer = production_service.lesson_video_service.produce_trailer

    def edit_then_trailer(**kwargs):
        Lesson.objects.filter(module__course=course, order=0).update(script="Edited. " + "word " * 600)
        return real_trailer(**kwargs)

    with mock.patch.object(production_service.lesson_video_service, "produce_trailer", side_effect=edit_then_trailer):
        run = _produce(course, creator)
    assert run.status == ProductionRunStatus.QUEUED  # retried, not failed
    course.refresh_from_db()
    assert course.status == CourseStatus.AWAITING_VIDEO

    run_production(str(run.id))
    run.refresh_from_db()
    assert run.status == ProductionRunStatus.COMPLETED, run.status_reason
    edited = Lesson.objects.get(module__course=course, order=0)
    assert "Edited" in media["store"].read_text(edited.video_script_file)


# --- rework -----------------------------------------------------------------------------


def _delivered(creator, media, scripts=None) -> Course:
    course = _awaiting_engine_video(creator, scripts)
    run = _produce(course, creator)
    assert run.status == ProductionRunStatus.COMPLETED, run.status_reason
    course.refresh_from_db()
    return course


@pytest.mark.django_db
def test_media_flags_at_the_second_seat_rework_just_those_lessons_and_resubmit_there(creator, media, make_user, api_client):
    writer = make_user(role=UserRole.STAFF_WRITER)
    course = _delivered(creator, media, scripts={0: "Kubernetes runs containers. " + "word " * 600})
    flagged, other = Lesson.objects.filter(module__course=course).order_by("order")
    other_video = other.video_url
    calls_before = len(media["primary"].calls)

    assert _post(api_client, writer, f"{QUEUE}{course.id}/claim/").status_code == 200
    response = _post(
        api_client,
        writer,
        f"{QUEUE}{course.id}/reject/",
        {
            "feedback": {"summary": "Fix the pronunciation."},
            "flags": [
                {
                    "flag_type": "PRONUNCIATION",
                    "title": "Kubernetes is mispronounced",
                    "lesson_id": str(flagged.id),
                    "reviewer_note": "Kubernetes = koo-ber-NET-eez",
                }
            ],
        },
    )

    assert response.status_code == 200
    course.refresh_from_db()
    assert course.status == CourseStatus.NEEDS_REVISION
    rework = ProductionRun.objects.filter(course=course, status=ProductionRunStatus.QUEUED).get()
    assert rework.instructions["lessons"] == {str(flagged.id): [ReworkAction.REVOICE]}
    assert PronunciationEntry.objects.get(course=course, term="Kubernetes").spoken_as == "koo-ber-NET-eez"

    run_production(str(rework.id))

    rework.refresh_from_db()
    assert rework.status == ProductionRunStatus.COMPLETED, rework.status_reason
    new_calls = media["primary"].calls[calls_before:]
    assert len(new_calls) == 1 and "koo-ber-NET-eez" in new_calls[0] and "Kubernetes" not in new_calls[0]
    flagged.refresh_from_db()
    other.refresh_from_db()
    assert other.video_url == other_video  # untouched lesson reused
    assert "Kubernetes" in media["store"].read_text(flagged.video_script_file)  # captions keep the spelling
    course.refresh_from_db()
    assert course.status == CourseStatus.SUBMITTED
    assert course.review_stage == ReviewStage.SECOND_REVIEW
    assert course.revision_seat == ""


@pytest.mark.django_db
def test_media_flags_at_qa_redraw_the_lesson_and_return_it_to_qa(creator, media, make_user, api_client):
    qa = make_user(role=UserRole.QA_REVIEWER)
    course = _delivered(creator, media)
    Course.objects.filter(pk=course.pk).update(status=CourseStatus.QA_VERIFICATION, review_stage="")
    flagged = Lesson.objects.filter(module__course=course).order_by("order").first()
    planned_before = len(media["storyboard"].calls)

    response = _post(
        api_client,
        qa,
        f"{QUEUE}{course.id}/qa-reject/",
        {
            "feedback": {"summary": "The diagram is wrong."},
            "flags": [{"flag_type": "visual_error", "title": "Wrong diagram", "lesson_id": str(flagged.id)}],
        },
    )

    assert response.status_code == 200
    rework = ProductionRun.objects.get(course=course, status=ProductionRunStatus.QUEUED)
    assert rework.instructions["lessons"] == {str(flagged.id): [ReworkAction.RESTORYBOARD]}
    run_production(str(rework.id))
    rework.refresh_from_db()
    assert rework.status == ProductionRunStatus.COMPLETED, rework.status_reason
    assert media["storyboard"].calls[planned_before:] == [flagged.title]
    course.refresh_from_db()
    assert course.status == CourseStatus.QA_VERIFICATION


@pytest.mark.django_db
def test_content_flags_wait_for_the_author_and_their_resubmission_remakes_the_changed_lesson(creator, media, make_user, api_client):
    writer = make_user(role=UserRole.STAFF_WRITER)
    course = _delivered(creator, media)
    first, second = Lesson.objects.filter(module__course=course).order_by("order")
    _post(api_client, writer, f"{QUEUE}{course.id}/claim/")
    response = _post(
        api_client,
        writer,
        f"{QUEUE}{course.id}/reject/",
        {
            "feedback": {"summary": "Lesson two is inaccurate."},
            "flags": [{"flag_type": "CONTENT_ACCURACY", "title": "Wrong claim", "lesson_id": str(second.id)}],
        },
    )
    assert response.status_code == 200
    assert not ProductionRun.objects.filter(course=course, status=ProductionRunStatus.QUEUED).exists()

    Lesson.objects.filter(pk=second.pk).update(script="Corrected lesson. " + "word " * 600)
    calls_before = len(media["primary"].calls)
    response = _post(api_client, creator, f"/api/v1/courses/{course.id}/submit/")

    assert response.status_code == 200
    course.refresh_from_db()
    assert course.status == CourseStatus.NEEDS_REVISION  # the engine resubmits once the video matches
    assert Notification.objects.filter(receiver=creator, title="Video being updated").exists()
    remake = ProductionRun.objects.get(course=course, status=ProductionRunStatus.QUEUED)
    run_production(str(remake.id))
    remake.refresh_from_db()
    assert remake.status == ProductionRunStatus.COMPLETED, remake.status_reason
    new_calls = media["primary"].calls[calls_before:]
    assert len(new_calls) == 1 and new_calls[0].startswith("Corrected lesson.")
    course.refresh_from_db()
    assert (course.status, course.review_stage) == (CourseStatus.SUBMITTED, ReviewStage.SECOND_REVIEW)


@pytest.mark.django_db
def test_a_rejection_of_a_creators_own_video_starts_no_rework(creator, media, make_user, api_client):
    writer = make_user(role=UserRole.STAFF_WRITER)
    course = _delivered(creator, media)
    Course.objects.filter(pk=course.pk).update(video_provider=VideoProvider.CREATOR)
    _post(api_client, writer, f"{QUEUE}{course.id}/claim/")

    response = _post(
        api_client,
        writer,
        f"{QUEUE}{course.id}/reject/",
        {"feedback": {"summary": "Audio too quiet."}, "flags": [{"flag_type": "AUDIO_LEVEL", "title": "Quiet"}]},
    )

    assert response.status_code == 200
    assert not ProductionRun.objects.filter(course=course, status=ProductionRunStatus.QUEUED).exists()
