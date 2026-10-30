"""Soludesk Studio deliveries (`studio.review.v1`): generated course text and
video, sent to one endpoint and told apart by `kind`.

A delivery is an envelope around the bodies every developer already pushes:
`content` of a text delivery is a course push (course_push_service.push_course)
and `content` of a video delivery is a video push (push_video). So a Studio
course goes through exactly the review flow, webhooks and payouts of any
pushed course; this module adds what the envelope promises on top.

* `content_hash` must be the SHA-256 of `content` as canonical JSON
  (content_hash), so both sides agree on what was delivered.
* `course_id` must be the id the idea was given at approval.
* A revision is accepted once per idea and kind. The same revision with the
  same hash is a retry and gets its receipt back without pushing again; with
  a different hash it is refused (RevisionConflict), and a revision below
  the newest accepted one is refused too (StaleRevision).
* A video delivery's platform-storage media must be stored first
  (studio_upload_service.verify_media).

The push and the receipt are written in one transaction, so a refused push
leaves no receipt and a receipt always means the platform took the content.
"""

import hashlib
import json

from django.db import transaction
from django.db.models import Max

from api.mie.enums import StudioReviewKind
from api.mie.exceptions import CourseIdMismatch, RevisionConflict, StaleRevision
from api.mie.models import DeveloperAccount, StudioReviewReceipt
from api.mie.services import course_push_service, studio_upload_service

SCHEMA_VERSION = "studio.review.v1"


def content_hash(content) -> str:
    """SHA-256 hex of `content` as canonical JSON: keys sorted, no
    whitespace (`,` and `:` separators), non-ASCII left as UTF-8."""

    canonical = json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deliver(
    *, developer: DeveloperAccount, envelope: dict
) -> tuple[StudioReviewReceipt, bool]:
    """Take one delivery. Returns the receipt and whether it is a replay of
    one already accepted.

    `envelope` is StudioReviewEnvelopeSerializer.validated_data, whose
    `content` is the validated push body for its kind. Raises NotFound for
    an idea that is not the caller's, CourseIdMismatch, RevisionConflict and
    StaleRevision (409), MediaNotStored (400), and every error the push
    itself raises. On any of those nothing is written.
    """

    kind = envelope["kind"]
    revision = envelope["revision"]
    with transaction.atomic():
        submission = course_push_service.lock_own_submission(
            developer=developer, submission_id=envelope["submission_id"]
        )
        if submission.stable_course_id != envelope["course_id"]:
            raise CourseIdMismatch()

        receipts = StudioReviewReceipt.objects.filter(submission=submission, kind=kind)
        accepted = receipts.filter(revision=revision).first()
        if accepted is not None:
            if accepted.content_hash != envelope["content_hash"]:
                raise RevisionConflict()
            return accepted, True
        newest = receipts.aggregate(newest=Max("revision"))["newest"]
        if newest is not None and revision < newest:
            raise StaleRevision(
                f"Revision {newest} of this {kind} was already accepted; "
                f"revision {revision} is older."
            )

        media = []
        if kind == StudioReviewKind.VIDEO:
            media = studio_upload_service.verify_media(
                submission=submission, content=envelope["content"]
            )
            course = course_push_service.push_video(
                developer=developer,
                submission_id=submission.id,
                data=envelope["content"],
            )
        else:
            course = course_push_service.push_course(
                developer=developer,
                submission_id=submission.id,
                data=envelope["content"],
            )

        receipt = StudioReviewReceipt.objects.create(
            developer=developer,
            submission=submission,
            course=course,
            schema_version=envelope["schema_version"],
            kind=kind,
            revision=revision,
            content_hash=envelope["content_hash"],
            source_map=envelope.get("source_map", []),
            media=media,
            course_status=course.status,
        )
    return receipt, False


def receipt_payload(*, receipt: StudioReviewReceipt, duplicate: bool) -> dict:
    """The StudioReviewReceiptSerializer shape for one receipt."""

    return {
        "receipt_id": receipt.id,
        "schema_version": receipt.schema_version,
        "submission_id": receipt.submission_id,
        "course_id": receipt.course_id,
        "kind": receipt.kind,
        "revision": receipt.revision,
        "content_hash": receipt.content_hash,
        "status": "accepted",
        "course_status": receipt.course_status,
        "source_map": receipt.source_map,
        "media": receipt.media,
        "duplicate": duplicate,
        "received_at": receipt.created_datetime,
    }
