from django.db import models
from django.utils.translation import gettext_lazy as _

from api.courses.enums import MediaSource
from core.mixins import (
    DateHistoryModelMixin,
    UserHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class CourseThumbnail(
    UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin
):
    """A course's thumbnail, as set via the wizard's 'Add Media' modal.

    History is kept: replacing a thumbnail doesn't delete the old row, it
    deactivates it (is_active=False) - which also satisfies the 'lets old
    thumbnails be kept/replaced' UI affordance. A partial unique index
    enforces at most one active thumbnail per course at a time; the active
    one is what any course card renders.

    Either a file (source=UPLOAD) or an external URL (Google Drive,
    YouTube, Dropbox, pasted link) is required, never both, never neither.
    """

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="thumbnails",
        help_text=_("Course this thumbnail belongs to."),
    )
    media_type = models.CharField(
        verbose_name=_("Media Type"),
        max_length=10,
        choices=(
            ("IMAGE", "Image"),
            ("VIDEO", "Video"),
        ),
        default="IMAGE",
        help_text=_("Whether the thumbnail is a still image or a short video."),
    )
    source = models.CharField(
        verbose_name=_("Source"),
        max_length=20,
        choices=MediaSource.choices,
        default=MediaSource.UPLOAD,
        help_text=_("Where the thumbnail came from - the 'Add Media' modal's options."),
    )
    file = models.CharField(
        verbose_name=_("File"),
        blank=True,
        default="",
        max_length=500,
        help_text=_("Uploaded file path; populated only when source is UPLOAD."),
    )
    external_url = models.CharField(
        verbose_name=_("External URL"),
        blank=True,
        default="",
        max_length=1000,
        help_text=_(
            "External URL (Google Drive, YouTube, Dropbox, pasted link); "
            "populated only when source is not UPLOAD."
        ),
    )
    width = models.PositiveIntegerField(
        verbose_name=_("Width"),
        null=True,
        blank=True,
        help_text=_("Source media width in pixels, when known."),
    )
    height = models.PositiveIntegerField(
        verbose_name=_("Height"),
        null=True,
        blank=True,
        help_text=_("Source media height in pixels, when known."),
    )
    is_active = models.BooleanField(
        verbose_name=_("Is Active"),
        default=True,
        help_text=_("Only one thumbnail per course is active at a time."),
    )

    class Meta:
        verbose_name = _("Course Thumbnail")
        verbose_name_plural = _("Course Thumbnails")
        ordering = ["-is_active", "-created_datetime"]
        constraints = [
            models.UniqueConstraint(
                fields=["course"],
                condition=models.Q(is_active=True),
                name="one_active_thumbnail_per_course",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(source="UPLOAD", file__gt="")
                    | models.Q(~models.Q(source="UPLOAD"), external_url__gt="")
                ),
                name="thumbnail_has_file_xor_external_url",
            ),
        ]
        indexes = [
            models.Index(fields=["course", "is_active"], name="thumb_course_active_idx"),
        ]

    def __str__(self):
        """Label the thumbnail by its course and source."""

        return f"Thumbnail for {self.course_id} ({self.source})"
