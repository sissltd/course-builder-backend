from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class StudioMediaUpload(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A file Studio was granted to upload into platform storage for one idea.

    Keyed by Studio's own asset_id, so asking again for the same asset
    resumes it: a fresh signed URL while the file is missing, or "stored"
    once it is there. A video delivery may reference platform storage only
    through these rows (studio_upload_service.verify_media).
    """

    developer = models.ForeignKey(
        "mie.DeveloperAccount",
        verbose_name=_("Developer"),
        on_delete=models.CASCADE,
        related_name="studio_media_uploads",
        help_text=_("Account the upload was granted to."),
    )
    submission = models.ForeignKey(
        "mie.CourseSubmission",
        verbose_name=_("Submission"),
        on_delete=models.CASCADE,
        related_name="studio_media_uploads",
        help_text=_("The approved idea the file is for."),
    )
    asset_id = models.CharField(
        verbose_name=_("Asset ID"),
        max_length=255,
        help_text=_("Studio's id for the file, unique per idea."),
    )
    file_key = models.CharField(
        verbose_name=_("File Key"),
        max_length=500,
        help_text=_("Object key of the latest grant."),
    )
    media_url = models.URLField(
        verbose_name=_("Media URL"),
        max_length=1000,
        help_text=_("Durable URL a delivery references the file by."),
    )
    purpose = models.CharField(
        verbose_name=_("Purpose"),
        max_length=40,
        help_text=_("Upload purpose whose rules the file was checked against."),
    )
    content_type = models.CharField(
        verbose_name=_("Content Type"),
        max_length=100,
        help_text=_("MIME type declared and signed into the upload."),
    )
    size = models.PositiveBigIntegerField(
        verbose_name=_("Size"),
        help_text=_("Byte count declared and signed into the upload."),
    )
    verified_at = models.DateTimeField(
        verbose_name=_("Verified At"),
        null=True,
        blank=True,
        help_text=_("When the stored object was last found with the declared size."),
    )

    class Meta:
        verbose_name = _("Studio Media Upload")
        verbose_name_plural = _("Studio Media Uploads")
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "asset_id"],
                name="mie_studio_upload_per_asset",
            ),
        ]
        indexes = [
            models.Index(
                fields=["submission", "media_url"], name="mie_studio_upload_url_idx"
            ),
        ]

    def __str__(self):
        return f"{self.asset_id} for {self.submission_id}"
