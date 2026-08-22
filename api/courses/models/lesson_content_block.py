from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import (
    DateHistoryModelMixin,
    UserHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class LessonContentBlock(
    UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin
):
    """One block in a lesson's block-based body editor.

    The lesson body ("General" panel) is composed block by block - Heading
    1/2, Paragraph, Number/Bullet lists, Blockquote, Divider, Image, Video,
    Embed, Quiz - rather than a single textarea. `order` is the block's
    position; `text_content` carries prose blocks and `media_url` carries
    media blocks, exactly one of which is populated per block type. A
    'quiz' block references the lesson's quiz instead of holding content.

    `quiz` uses SET_NULL so deleting a quiz removes the block's reference
    but leaves the (now-empty) block for the creator to fix rather than
    silently deleting body content.
    """

    class BlockType(models.TextChoices):
        HEADING_1 = "HEADING_1", "Heading 1"
        HEADING_2 = "HEADING_2", "Heading 2"
        PARAGRAPH = "PARAGRAPH", "Paragraph"
        NUMBERED_LIST = "NUMBERED_LIST", "Numbered List"
        BULLETED_LIST = "BULLETED_LIST", "Bulleted List"
        BLOCKQUOTE = "BLOCKQUOTE", "Blockquote"
        DIVIDER = "DIVIDER", "Divider"
        IMAGE = "IMAGE", "Image"
        VIDEO = "VIDEO", "Video"
        EMBED = "EMBED", "Embed"
        QUIZ = "QUIZ", "Quiz"

    lesson = models.ForeignKey(
        "courses.Lesson",
        verbose_name=_("Lesson"),
        on_delete=models.CASCADE,
        related_name="content_blocks",
        help_text=_("Lesson this block belongs to."),
    )
    order = models.PositiveIntegerField(
        verbose_name=_("Order"),
        default=0,
        help_text=_("Block's position within the lesson body."),
    )
    block_type = models.CharField(
        verbose_name=_("Block Type"),
        max_length=20,
        choices=BlockType.choices,
        help_text=_("What kind of content this block holds."),
    )
    text_content = models.TextField(
        verbose_name=_("Text Content"),
        blank=True,
        default="",
        help_text=_(
            "Prose payload for heading/paragraph/list/blockquote blocks."
        ),
    )
    media_url = models.CharField(
        verbose_name=_("Media URL"),
        blank=True,
        default="",
        max_length=500,
        help_text=_(
            "Media payload for image/video/embed blocks - uploaded path or "
            "external URL."
        ),
    )
    quiz = models.ForeignKey(
        "quizzes.Quiz",
        verbose_name=_("Quiz"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="content_blocks",
        help_text=_(
            "The quiz this 'quiz' block embeds - only meaningful when "
            "block_type is QUIZ."
        ),
    )

    class Meta:
        verbose_name = _("Lesson Content Block")
        verbose_name_plural = _("Lesson Content Blocks")
        ordering = ["order", "created_datetime"]
        constraints = [
            models.UniqueConstraint(
                fields=["lesson", "order"],
                name="unique_block_order_per_lesson",
            ),
            models.CheckConstraint(
                check=~models.Q(
                    models.Q(block_type="QUIZ"),
                    models.Q(quiz__isnull=True),
                ),
                name="quiz_block_requires_quiz",
            ),
        ]
        indexes = [
            models.Index(fields=["lesson", "order"], name="block_lesson_order_idx"),
        ]

    def __str__(self):
        """Summarize the block by type and position."""

        return f"{self.block_type} #{self.order} in {self.lesson_id}"
