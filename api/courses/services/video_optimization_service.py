"""Make creator-uploaded videos start fast in the browser.

A creator's MP4 goes straight from their browser to storage, as recorded.
Many exporters write the file's index (moov) at the end, so a browser has to
fetch the tail of a file of up to 500 MB before it can show the first frame.
After a video is saved, a worker checks it and, when needed, rewrites it in
place with the index at the front (and re-encodes the rare non-H.264 file).
The object key never changes, so stored references stay valid, and playback
works throughout: the status only says whether it now starts fast.

Production Engine renders are already written this way and are skipped.
"""

import logging
import tempfile
from pathlib import Path

from django.apps import apps
from django.db import transaction

from api.courses.enums import VideoProcessingStatus
from api.production.rendering import ffmpeg
from shared.services.storage_service import (
    StorageError,
    StorageService,
    own_media_key,
)

logger = logging.getLogger(__name__)

VIDEO_FIELDS = {
    ("courses.Lesson", "video_url"): "video_processing_status",
    ("courses.Course", "preview_video_url"): "preview_video_processing_status",
    ("courses.LessonContentBlock", "media_url"): None,
}
"""Every field that can hold a creator's video, and the status field that
tracks it (None when the model has nowhere to show one)."""


def _video_key(value: str) -> str | None:
    """The key of a creator upload worth optimising, else None."""

    key = own_media_key(value)
    if key is None or not key.lower().endswith(".mp4") or key.startswith("production/"):
        return None
    return key


def queue_if_changed(*, instance, field: str, previous: str) -> None:
    """Queue optimisation of `instance.<field>` when it now holds a different
    upload of ours, after the caller's transaction commits. Clears the status
    when the field no longer holds one."""

    value = getattr(instance, field)
    if value == previous:
        return
    label = instance._meta.label
    status_field = VIDEO_FIELDS[(label, field)]
    key = _video_key(value)
    status = VideoProcessingStatus.PENDING if key else ""
    if status_field:
        type(instance).objects.filter(pk=instance.pk).update(**{status_field: status})
        setattr(instance, status_field, status)
    if key:
        from api.courses.tasks import optimize_creator_video

        pk = str(instance.pk)
        transaction.on_commit(
            lambda: optimize_creator_video.delay(label, pk, field, key)
        )


def optimize(*, model_label: str, pk: str, field: str, file_key: str) -> None:
    """Rewrite the stored video for streaming if it needs it, and record the
    outcome. Never raises for a media or storage failure: the original file
    still plays, so the creator is never blocked."""

    model = apps.get_model(model_label)
    status_field = VIDEO_FIELDS[(model_label, field)]
    instance = None
    if status_field:
        instance = model.objects.filter(pk=pk).first()
        if instance is None or own_media_key(getattr(instance, field)) != file_key:
            # Deleted, or replaced by another upload since this was queued:
            # that upload queued its own run.
            return
    # Content blocks are recreated on every editor save, so a block's row
    # may be gone while its video is still in use: the key is enough.

    try:
        with tempfile.TemporaryDirectory() as workdir:
            source = Path(workdir) / "source.mp4"
            StorageService.download_file(file_key, source)
            streams = ffmpeg.probe(source).get("streams", [])
            video = next((s for s in streams if s.get("codec_type") == "video"), None)
            if video is None:
                raise ffmpeg.MediaError("The file has no video stream.")
            transcode = video.get("codec_name") != "h264"
            if transcode or not ffmpeg.is_faststart(source):
                optimized = Path(workdir) / "optimized.mp4"
                ffmpeg.optimize_for_streaming(
                    source=source, out=optimized, transcode=transcode
                )
                # Keep what the creator declared at upload; QA audits it.
                metadata = StorageService.object_metadata(file_key)
                if transcode:
                    metadata["codec"] = "h264"
                StorageService.upload_file(
                    optimized,
                    file_key=file_key,
                    content_type="video/mp4",
                    metadata=metadata,
                )
        status = VideoProcessingStatus.READY
    except (ffmpeg.MediaError, StorageError) as exc:
        logger.warning(
            "[<>VideoOptimization<>] %s %s.%s (%s) not optimised: %s",
            model_label,
            pk,
            field,
            file_key,
            exc,
        )
        status = VideoProcessingStatus.FAILED

    if instance is not None:
        # Only if the field still holds this upload.
        model.objects.filter(pk=pk, **{field: getattr(instance, field)}).update(
            **{status_field: status}
        )
