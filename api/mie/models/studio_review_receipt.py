from django.db import models

from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class StudioReviewReceipt(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A committed review handoff, scoped through its owning submission.

    No raw source URLs or content are retained here. The request digest binds
    retries to the exact envelope; source IDs let feedback name Studio lessons.
    """

    submission = models.ForeignKey(
        "mie.CourseSubmission", on_delete=models.PROTECT, related_name="studio_receipts"
    )
    idempotency_key = models.CharField(max_length=200)
    request_hash = models.CharField(max_length=64)
    content_hash = models.CharField(max_length=64)
    kind = models.CharField(
        max_length=5, choices=[("text", "Text"), ("video", "Video")]
    )
    revision = models.PositiveIntegerField()
    course_id = models.UUIDField()
    source_map = models.JSONField(default=list)
    media = models.JSONField(default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "idempotency_key"], name="mie_studio_receipt_key"
            ),
            models.UniqueConstraint(
                fields=["submission", "kind", "revision"],
                name="mie_studio_receipt_version",
            ),
        ]


class StudioMediaUpload(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    submission = models.ForeignKey("mie.CourseSubmission", on_delete=models.PROTECT)
    asset_id = models.UUIDField()
    descriptor_hash = models.CharField(max_length=64)
    descriptor = models.JSONField(default=dict)
    purpose = models.CharField(max_length=30)
    file_key = models.CharField(max_length=500)
    upload_cipher = models.TextField()
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "asset_id"], name="mie_studio_upload_asset"
            )
        ]
