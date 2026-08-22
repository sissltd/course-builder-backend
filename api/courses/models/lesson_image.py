from django.db import models
from django.utils.translation import gettext_lazy as _

from api.courses.enums import MediaSource
from core.mixins import (
    DateHistoryModelMixin,
    UserHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class LessonImage(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin):
    """An image placed in a lesson's body (the 'Add image' modal).

    Multiple images per lesson are supported, ordered by `order` - the
    block-based editor positions media via LessonContentBlock, but this
    table is the media library entry itself (with its caption and source
    metadata) so images survive block re-ordering as first-class rows.
    """

    lesson = models.ForeignKey(
        "courses.Lesson",
        verbose_name=_("Lesson"),
        on_delete=models.CASCADE,
        related_name="images",
        help_text=_("Lesson this image belongs to."),
    )
    image = models.CharField(
        verbose_name=_("Image"),
        max_length=500,
        help_text=_(
            "Uploaded image file path, or the external URL for non-upload sources."
        ),
    )
    caption = models.CharField(
        verbose_name=_("Caption"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Optional caption shown under the image."),
    )
    source_type = models.CharField(
        verbose_name=_("Source Type"),
        max_length=20,
        choices=MediaSource.choices,
        default=MediaSource.UPLOAD,
        help_text=_("Where this image came from - the 'Add Media' modal's options."),
    )
    order = models.PositiveIntegerField(
        verbose_name=_("Order"),
        default=0,
        help_text=_("Display order among the lesson's images."),
    )

    class Meta:
        verbose_name = _("Lesson Image")
        verbose_name_plural = _("Lesson Images")
        ordering = ["order", "created_datetime"]
        indexes = [
            models.Index(fields=["lesson", "order"], name="lesson_image_order_idx"),
        ]

    def __str__(self):
        """Label the image by its lesson and truncated path."""

        return f"Image for {self.lesson_id} ({self.image[:40]})"
