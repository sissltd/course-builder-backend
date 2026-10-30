"""Versioned Studio transport over the existing course submission services."""

import hashlib
import json
from urllib.parse import urlsplit

from django.db import transaction
from rest_framework import exceptions

from api.mie.models import CourseSubmission, StudioReviewReceipt
from api.mie.serializers.course_push_serializer import CoursePushSerializer
from api.mie.serializers.studio_review_serializer import StudioVideoSerializer
from api.mie.services import course_push_service
from api.platform.services import platform_settings_service
from shared.services.storage_service import StorageService


class ReviewConflict(exceptions.APIException):
    status_code = 409
    default_code = "studio_review_conflict"


def _receipt(row):
    return {
        "accepted": True,
        "receipt_id": str(row.id),
        "course_id": str(row.course_id),
        "revision": row.revision,
        "kind": row.kind,
        "content_hash": row.content_hash,
        "media_persisted": row.kind == "video",
    }


def _source_map(source, content):
    """Bind external IDs to the exact ordered curriculum, never by title."""
    modules = content.get("modules", [])
    if len(modules) != len(source):
        raise exceptions.ValidationError("Source map must cover the full curriculum.")
    seen = set()
    for position, (module, mapped) in enumerate(zip(modules, source), 1):
        if mapped["order"] != module.get("order", position):
            raise exceptions.ValidationError("Source module order does not match.")
        if len(mapped["lessons"]) != len(module.get("lessons", [])):
            raise exceptions.ValidationError("Source map must cover every lesson.")
        items = [(mapped, module, position)] + [
            (a, b, i)
            for i, (a, b) in enumerate(zip(mapped["lessons"], module["lessons"]), 1)
        ]
        for external, internal, order in items:
            if external["id"] in seen or external["order"] != internal.get(
                "order", order
            ):
                raise exceptions.ValidationError(
                    "Source IDs and orders must be unique and match."
                )
            seen.add(external["id"])


def _owned_media(url, submission, purpose):
    """A receipt must never attest persistence of expiring producer URLs.

    Producers use the existing authenticated upload-presign route and upload
    the bytes first. Only existing objects under the platform storage origin
    can become accepted video references.
    """
    parsed = urlsplit(url)
    marker = "/uploads/"
    if (
        parsed.scheme != "https"
        or parsed.query
        or parsed.fragment
        or marker not in parsed.path
    ):
        raise exceptions.ValidationError(
            "Upload video and captions to platform storage first."
        )
    key = "uploads/" + parsed.path.split(marker, 1)[1]
    if ".." in key.split("/") or StorageService.public_url(key) != url:
        raise exceptions.ValidationError("Media must use the platform storage origin.")
    from api.mie.models import StudioMediaUpload

    grant = StudioMediaUpload.objects.filter(
        submission=submission, file_key=key, purpose=purpose
    ).first()
    if grant is None:
        raise exceptions.ValidationError(
            "Media was not uploaded for this submission and purpose."
        )
    if not grant.descriptor or not StorageService.verify_file(key, **grant.descriptor):
        raise exceptions.ValidationError("Platform media upload is missing.")
    return key


@transaction.atomic
def accept_review(*, developer, data, idempotency_key):
    if not idempotency_key or len(idempotency_key) > 200:
        raise exceptions.ValidationError("A bounded Idempotency-Key is required.")
    # One lock serializes first delivery and uncertain-response retries, including
    # different keys for the same version. Scope before disclosing any receipt.
    submission = (
        CourseSubmission.objects.select_for_update(of=("self",))
        .filter(pk=data["submission_id"], developer=developer)
        .first()
    )
    if submission is None:
        raise exceptions.NotFound()
    fingerprint = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    existing = submission.studio_receipts.filter(
        idempotency_key=idempotency_key
    ).first()
    if existing:
        if existing.request_hash != fingerprint:
            raise ReviewConflict("Idempotency key was used for different content.")
        return _receipt(existing)
    if submission.reserved_course_id != data["course_id"]:
        raise ReviewConflict("Course identity does not match the approved topic.")
    previous = (
        submission.studio_receipts.filter(kind=data["kind"])
        .order_by("-revision")
        .first()
    )
    if previous and data["revision"] <= previous.revision:
        raise ReviewConflict(
            "Review revision must advance; retry with the original key."
        )
    if not platform_settings_service.uses_staged_review(submission.resulting_course):
        raise ReviewConflict("Studio requires platform text-before-video review.")
    media = []
    if data["kind"] == "text":
        serializer = CoursePushSerializer(
            data=data["content"], context={"staged_review_flow": True}
        )
        serializer.is_valid(raise_exception=True)
        _source_map(data["source_map"], data["content"])
        if data["content"].get("preview_video_url") or any(
            lesson.get("video_url")
            or lesson.get("embedded_link")
            or lesson.get("lesson_type", lesson.get("content_type", "TEXT")) != "TEXT"
            or any(
                b.get("block_type") in ("VIDEO", "EMBED")
                for b in lesson.get("content_blocks", [])
            )
            for module in data["content"].get("modules", [])
            for lesson in module.get("lessons", [])
        ):
            raise exceptions.ValidationError(
                "Text review must not include video production."
            )
        course = course_push_service.push_course(
            developer=developer,
            submission_id=submission.id,
            data=serializer.validated_data,
        )
    else:
        text = (
            submission.studio_receipts.filter(kind="text").order_by("-revision").first()
        )
        if not text or text.source_map != data["source_map"]:
            raise ReviewConflict("Video must address the accepted text curriculum.")
        serializer = StudioVideoSerializer(data=data["content"])
        serializer.is_valid(raise_exception=True)
        expected = {
            (m["order"], lesson["order"])
            for m in text.source_map
            for lesson in m["lessons"]
        }
        supplied = {
            (lesson["module_order"], lesson["lesson_order"])
            for lesson in serializer.validated_data.get("lessons", [])
        }
        if supplied != expected:
            raise exceptions.ValidationError(
                "Video must cover every accepted text lesson."
            )
        media.append(
            {
                "role": "preview",
                "key": _owned_media(
                    serializer.validated_data["preview_video_url"],
                    submission,
                    "COURSE_PREVIEW_VIDEO",
                ),
            }
        )
        for lesson in serializer.validated_data["lessons"]:
            if (
                not lesson.get("transcript")
                or not lesson.get("captions_url")
                or not lesson.get("video_url")
            ):
                raise exceptions.ValidationError(
                    "Every lesson needs video, transcript and captions."
                )
            media.append(
                {
                    "module_order": lesson["module_order"],
                    "lesson_order": lesson["lesson_order"],
                    "video_key": _owned_media(
                        lesson["video_url"], submission, "LESSON_VIDEO"
                    ),
                    "captions_key": _owned_media(
                        lesson["captions_url"], submission, "SUBTITLE"
                    ),
                    "transcript": lesson["transcript"],
                }
            )
        course = course_push_service.push_video(
            developer=developer,
            submission_id=submission.id,
            data={**serializer.validated_data, "promote_text_lessons": True},
        )
    row = StudioReviewReceipt.objects.create(
        submission=submission,
        idempotency_key=idempotency_key,
        request_hash=fingerprint,
        course_id=course.id,
        kind=data["kind"],
        revision=data["revision"],
        content_hash=data["content_hash"],
        source_map=data["source_map"],
        media=media,
    )
    return _receipt(row)
