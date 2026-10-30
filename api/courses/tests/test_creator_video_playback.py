"""Creator videos always come back playable, and new uploads are optimised.

The bucket is private, so the builder's responses sign our own media on
every read, and a presigned URL a client sends is stored in its durable
form. A new upload of ours is queued for streaming optimisation once the
write commits.
"""

from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest

from api.courses.enums import VideoProcessingStatus
from api.courses.models import Lesson, LessonContentBlock
from api.courses.services import video_optimization_service
from api.courses.tests.factories import build_compliant_course
from api.production.rendering import ffmpeg
from api.users.enums import UserRole
from shared.services import storage_service
from shared.services.storage_service import StorageService

BUCKET_URL = "https://coursebuilder.lon1.digitaloceanspaces.com"
KEY = "uploads/courses/0a1b2c3d4e5f.mp4"
PUBLIC_URL = f"{BUCKET_URL}/{KEY}"
EXTERNAL_URL = "https://cdn.example.com/lesson.mp4"


@pytest.fixture(autouse=True)
def bucket():
    with (
        mock.patch.object(storage_service, "ACCESS_KEY_ID", "access-key"),
        mock.patch.object(storage_service, "ACCESS_SECRET_KEY", "secret-key"),
        mock.patch.object(storage_service, "BUCKET_NAME", "coursebuilder"),
        mock.patch.object(storage_service, "BUCKET_URL", BUCKET_URL),
    ):
        yield


@pytest.fixture
def queued():
    with mock.patch("api.courses.tasks.optimize_creator_video.delay") as delay:
        yield delay


@pytest.fixture
def creator(make_user):
    return make_user(role=UserRole.COURSE_CREATOR)


@pytest.fixture
def course(creator):
    return build_compliant_course(creator=creator, module_count=1, lessons_per_module=1)


@pytest.fixture
def lesson(course):
    return Lesson.objects.get(module__course=course)


def _lesson_url(lesson):
    module = lesson.module
    return (
        f"/api/v1/courses/{module.course_id}/modules/{module.id}/lessons/{lesson.id}/"
    )


def _signed_for(url, key=KEY):
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.path == f"/coursebuilder/{key}"
    assert "X-Amz-Signature" in query
    return query


# --- Reads: our media is signed afresh, other links pass through -----------


@pytest.mark.django_db
def test_a_lesson_video_stored_as_its_public_url_is_served_signed(
    api_client, creator, lesson
):
    Lesson.objects.filter(pk=lesson.pk).update(video_url=PUBLIC_URL)
    api_client.force_authenticate(creator)

    response = api_client.get(_lesson_url(lesson))

    assert response.status_code == 200
    query = _signed_for(response.data["video_url"])
    assert query["X-Amz-Expires"] == [str(storage_service.PLAYBACK_URL_SECONDS)]


@pytest.mark.django_db
def test_an_expired_signed_url_already_stored_is_served_with_a_fresh_signature(
    api_client, creator, lesson
):
    stale = StorageService.generate_presigned_get(KEY, expires_in=600)
    Lesson.objects.filter(pk=lesson.pk).update(video_url=stale)
    api_client.force_authenticate(creator)

    response = api_client.get(_lesson_url(lesson))

    query = _signed_for(response.data["video_url"])
    assert query["X-Amz-Expires"] == [str(storage_service.PLAYBACK_URL_SECONDS)]


@pytest.mark.django_db
def test_an_external_video_link_is_served_unchanged(api_client, creator, lesson):
    Lesson.objects.filter(pk=lesson.pk).update(video_url=EXTERNAL_URL)
    api_client.force_authenticate(creator)

    response = api_client.get(_lesson_url(lesson))

    assert response.data["video_url"] == EXTERNAL_URL


@pytest.mark.django_db
def test_the_course_detail_signs_the_preview_video_and_its_lessons(
    api_client, creator, course, lesson
):
    course.preview_video_url = PUBLIC_URL
    course.save(update_fields=["preview_video_url"])
    Lesson.objects.filter(pk=lesson.pk).update(video_url=PUBLIC_URL)
    api_client.force_authenticate(creator)

    response = api_client.get(f"/api/v1/courses/{course.id}/")

    assert response.status_code == 200
    _signed_for(response.data["preview_video_url"])
    _signed_for(response.data["modules"][0]["lessons"][0]["video_url"])


# --- Writes: durable storage, and new uploads are queued -------------------


@pytest.mark.django_db
def test_saving_the_upload_file_url_stores_the_durable_url_and_queues_optimisation(
    api_client, creator, lesson, queued, django_capture_on_commit_callbacks
):
    file_url = StorageService.generate_presigned_get(KEY)
    api_client.force_authenticate(creator)

    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.patch(
            _lesson_url(lesson), {"video_url": file_url}, format="json"
        )

    assert response.status_code == 200, response.data
    _signed_for(response.data["video_url"])
    assert response.data["video_processing_status"] == VideoProcessingStatus.PENDING
    lesson.refresh_from_db()
    assert lesson.video_url == PUBLIC_URL
    assert lesson.video_processing_status == VideoProcessingStatus.PENDING
    queued.assert_called_once_with("courses.Lesson", str(lesson.pk), "video_url", KEY)


@pytest.mark.django_db
def test_an_external_link_is_not_queued(
    api_client, creator, lesson, queued, django_capture_on_commit_callbacks
):
    api_client.force_authenticate(creator)

    with django_capture_on_commit_callbacks(execute=True):
        api_client.patch(
            _lesson_url(lesson), {"video_url": EXTERNAL_URL}, format="json"
        )

    lesson.refresh_from_db()
    assert lesson.video_url == EXTERNAL_URL
    assert lesson.video_processing_status == ""
    queued.assert_not_called()


@pytest.mark.django_db
def test_resaving_a_lesson_body_queues_only_a_video_it_did_not_have(
    api_client, creator, lesson, queued, django_capture_on_commit_callbacks
):
    url = f"{_lesson_url(lesson)}content-blocks/bulk/"
    body = [{"order": 0, "block_type": "VIDEO", "media_url": PUBLIC_URL}]
    api_client.force_authenticate(creator)

    with django_capture_on_commit_callbacks(execute=True):
        assert api_client.put(url, body, format="json").status_code == 200
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.put(url, body, format="json")

    assert response.status_code == 200
    _signed_for(response.data[0]["media_url"])
    assert queued.call_count == 1
    assert LessonContentBlock.objects.get(lesson=lesson).media_url == PUBLIC_URL


# --- The optimisation run --------------------------------------------------


@pytest.fixture
def stored(tmp_path):
    """Storage faked on disk: the run's download, metadata and re-upload."""

    uploads = {}

    def download_file(file_key, path):
        Path(path).write_bytes(b"video")

    def upload_file(path, *, file_key, content_type, metadata=None):
        uploads[file_key] = {"content_type": content_type, "metadata": metadata}
        return file_key

    with (
        mock.patch.object(StorageService, "download_file", download_file),
        mock.patch.object(StorageService, "upload_file", upload_file),
        mock.patch.object(
            StorageService,
            "object_metadata",
            return_value={"upload-purpose": "LESSON_VIDEO"},
        ),
    ):
        yield uploads


def _probe(codec):
    return {"streams": [{"codec_type": "video", "codec_name": codec}]}


def _optimize(lesson):
    video_optimization_service.optimize(
        model_label="courses.Lesson", pk=str(lesson.pk), field="video_url", file_key=KEY
    )
    lesson.refresh_from_db()


@pytest.fixture
def pending_lesson(lesson):
    Lesson.objects.filter(pk=lesson.pk).update(
        video_url=PUBLIC_URL, video_processing_status=VideoProcessingStatus.PENDING
    )
    return lesson


@pytest.mark.django_db
def test_a_file_already_laid_out_for_streaming_is_left_alone(pending_lesson, stored):
    with (
        mock.patch.object(ffmpeg, "probe", return_value=_probe("h264")),
        mock.patch.object(ffmpeg, "is_faststart", return_value=True),
        mock.patch.object(ffmpeg, "optimize_for_streaming") as rewrite,
    ):
        _optimize(pending_lesson)

    rewrite.assert_not_called()
    assert stored == {}
    assert pending_lesson.video_processing_status == VideoProcessingStatus.READY


@pytest.mark.django_db
def test_a_file_with_its_index_at_the_end_is_rewritten_in_place(pending_lesson, stored):
    def rewrite(*, source, out, transcode):
        out.write_bytes(b"optimised")

    with (
        mock.patch.object(ffmpeg, "probe", return_value=_probe("h264")),
        mock.patch.object(ffmpeg, "is_faststart", return_value=False),
        mock.patch.object(
            ffmpeg, "optimize_for_streaming", side_effect=rewrite
        ) as call,
    ):
        _optimize(pending_lesson)

    assert call.call_args.kwargs["transcode"] is False
    assert stored[KEY] == {
        "content_type": "video/mp4",
        "metadata": {"upload-purpose": "LESSON_VIDEO"},
    }
    assert pending_lesson.video_processing_status == VideoProcessingStatus.READY


@pytest.mark.django_db
def test_a_video_not_in_h264_is_reencoded(pending_lesson, stored):
    with (
        mock.patch.object(ffmpeg, "probe", return_value=_probe("hevc")),
        mock.patch.object(ffmpeg, "is_faststart", return_value=True),
        mock.patch.object(ffmpeg, "optimize_for_streaming") as call,
    ):
        _optimize(pending_lesson)

    assert call.call_args.kwargs["transcode"] is True
    assert stored[KEY]["metadata"]["codec"] == "h264"


@pytest.mark.django_db
def test_a_file_ffmpeg_cannot_read_is_marked_failed_without_raising(
    pending_lesson, stored
):
    with mock.patch.object(ffmpeg, "probe", side_effect=ffmpeg.MediaError("bad file")):
        _optimize(pending_lesson)

    assert stored == {}
    assert pending_lesson.video_processing_status == VideoProcessingStatus.FAILED


@pytest.mark.django_db
def test_a_video_replaced_since_it_was_queued_is_skipped(pending_lesson, stored):
    Lesson.objects.filter(pk=pending_lesson.pk).update(video_url=EXTERNAL_URL)

    with mock.patch.object(ffmpeg, "probe") as probe:
        _optimize(pending_lesson)

    probe.assert_not_called()


def _box(kind, payload=b""):
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


@pytest.mark.parametrize(
    ("boxes", "expected"),
    [
        (
            [
                _box(b"ftyp", b"isom"),
                _box(b"moov", b"x" * 16),
                _box(b"mdat", b"y" * 32),
            ],
            True,
        ),
        (
            [
                _box(b"ftyp", b"isom"),
                _box(b"mdat", b"y" * 32),
                _box(b"moov", b"x" * 16),
            ],
            False,
        ),
    ],
)
def test_faststart_is_read_from_the_box_order(tmp_path, boxes, expected):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"".join(boxes))

    assert ffmpeg.is_faststart(path) is expected
