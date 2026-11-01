"""Record studio.receipt.v1 callbacks for Studio deliveries.

A receipt tells Studio that a topic, text course or video it sent is durably
stored. It confirms storage only: review, video production and publication
carry on through their own workflows. `record` writes the receipt in the
caller's transaction, so a rolled-back delivery never produces one;
studio_receipt_sender signs and sends it after commit.

Receipts are recorded only for the Studio developer account
(settings.STUDIO_RECEIPT_DEVELOPER_ACCOUNT_ID), and only when its request
carries X-Studio-Delivery-Id. Any other caller's X-Studio-* headers are
ignored.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import exceptions

from api.courses.models import Lesson, Module
from api.mie.enums import StudioReceiptKind
from api.mie.models import StudioDeliveryReceipt

CONTRACT_VERSION = "studio.receipt.v1"

DELIVERY_ID_HEADER = "X-Studio-Delivery-Id"
PAYLOAD_SHA256_HEADER = "X-Studio-Payload-SHA256"
CONTENT_REVISION_HEADER = "X-Studio-Content-Revision"

SHA256_HEX = re.compile(r"^[a-f0-9]{64}$")


class DeliveryConflict(exceptions.APIException):
    status_code = 409
    default_code = "studio_delivery_conflict"
    default_detail = "This X-Studio-Delivery-Id was already used for another delivery."


@dataclass(frozen=True)
class StudioDelivery:
    """What a Studio request declared about itself, checked against its bytes."""

    delivery_id: uuid.UUID
    payload_sha256: str
    revision: int | None


def read_delivery(request, *, needs_revision: bool) -> StudioDelivery | None:
    """The checked Studio delivery headers, or None when no receipt applies.

    Call before request.data is read: the hash is over the raw body bytes.
    A malformed header, a hash that does not match the body, or (for text
    and video) a missing revision is 400, before anything is stored.
    """

    developer = request.auth
    raw_id = request.headers.get(DELIVERY_ID_HEADER)
    if (
        raw_id is None
        or not settings.STUDIO_RECEIPT_DEVELOPER_ACCOUNT_ID
        or str(getattr(developer, "pk", ""))
        != settings.STUDIO_RECEIPT_DEVELOPER_ACCOUNT_ID
    ):
        return None
    try:
        delivery_id = uuid.UUID(raw_id)
    except ValueError as exc:
        raise exceptions.ValidationError(
            {DELIVERY_ID_HEADER: "Must be the Studio job UUID."}
        ) from exc
    declared = request.headers.get(PAYLOAD_SHA256_HEADER, "")
    if not SHA256_HEX.match(declared):
        raise exceptions.ValidationError(
            {PAYLOAD_SHA256_HEADER: "Must be a lowercase hex SHA-256."}
        )
    if hashlib.sha256(request.body).hexdigest() != declared:
        raise exceptions.ValidationError(
            {PAYLOAD_SHA256_HEADER: "Does not match the SHA-256 of the request body."}
        )
    revision = None
    if needs_revision:
        raw_revision = request.headers.get(CONTENT_REVISION_HEADER, "")
        if not raw_revision.isdigit() or int(raw_revision) < 1:
            raise exceptions.ValidationError(
                {CONTENT_REVISION_HEADER: "Must be the positive revision being sent."}
            )
        revision = int(raw_revision)
    return StudioDelivery(
        delivery_id=delivery_id, payload_sha256=declared, revision=revision
    )


def previous_topic(delivery: StudioDelivery):
    """The submission an earlier request with this delivery id stored, so a
    retry replays it instead of storing the idea twice. A different body
    under the same delivery id is 409."""

    receipt = (
        StudioDeliveryReceipt.objects.filter(delivery_id=delivery.delivery_id)
        .select_related("submission")
        .first()
    )
    if receipt is None:
        return None
    if (
        receipt.kind != StudioReceiptKind.TOPIC
        or receipt.payload["payload_sha256"] != delivery.payload_sha256
    ):
        raise DeliveryConflict()
    return receipt.submission


def record(
    *, delivery: StudioDelivery, submission, kind, course=None, source_map=None
) -> StudioDeliveryReceipt:
    """Write the receipt for a delivery stored in the current transaction.

    For text and video the module and lesson counts come from the persisted
    course and must match the delivery's source map. Video receipts are only
    recorded after every media file has been verified, so they always carry
    media_persisted: true.
    """

    receipt_id = uuid.uuid4()
    payload = {
        "contract_version": CONTRACT_VERSION,
        "receipt_id": str(receipt_id),
        "delivery_id": str(delivery.delivery_id),
        "environment": settings.STUDIO_RECEIPT_ENVIRONMENT,
        "kind": kind,
        "submission_id": str(submission.id),
        "payload_sha256": delivery.payload_sha256,
        "status": "received",
        "received_at": timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if kind != StudioReceiptKind.TOPIC:
        module_count = Module.objects.filter(course=course).count()
        lesson_count = Lesson.objects.filter(module__course=course).count()
        if module_count != len(source_map) or lesson_count != sum(
            len(module["lessons"]) for module in source_map
        ):
            raise DeliveryConflict(
                "The stored course does not match the delivery's modules and lessons."
            )
        payload.update(
            course_id=str(course.id),
            revision=delivery.revision,
            module_count=module_count,
            lesson_count=lesson_count,
        )
        if kind == StudioReceiptKind.VIDEO:
            payload["media_persisted"] = True
    try:
        # A savepoint, so a reused delivery id surfaces as a conflict rather
        # than as an IntegrityError the caller may read as its own race.
        with transaction.atomic():
            return StudioDeliveryReceipt.objects.create(
                id=receipt_id,
                submission=submission,
                delivery_id=delivery.delivery_id,
                kind=kind,
                payload=payload,
            )
    except IntegrityError as exc:
        raise DeliveryConflict() from exc
