from django.db import models
from django.utils.translation import gettext_lazy as _

from api.courses.enums import AssessmentLevel
from core.mixins import (
    DateHistoryModelMixin,
    UserHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class Quiz(
    UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin
):
    """A relational quiz attached to exactly one of Lesson, Module, or Course.

    The relational counterpart of courses.Assessment: where Assessment stores
    its questions as a JSON blob, Quiz normalizes them into Question and
    QuestionOption rows so answers can be graded per-option.
    """

    level = models.CharField(
        verbose_name=_("Level"),
        max_length=10,
        choices=AssessmentLevel.choices,
        help_text=_("Which entity level this quiz is attached to."),
    )
    title = models.CharField(
        verbose_name=_("Title"),
        max_length=255,
        help_text=_("Quiz title."),
    )
    description = models.TextField(
        verbose_name=_("Description"),
        blank=True,
        help_text=_("Optional quiz instructions shown to learners."),
    )
    lesson = models.ForeignKey(
        "courses.Lesson",
        verbose_name=_("Lesson"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="quizzes",
    )
    module = models.ForeignKey(
        "courses.Module",
        verbose_name=_("Module"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="quizzes",
    )
    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="quizzes",
    )
    passing_score = models.PositiveIntegerField(
        verbose_name=_("Passing Score"),
        default=60,
        help_text=_("Minimum percentage required to pass."),
    )
    time_limit_minutes = models.PositiveIntegerField(
        verbose_name=_("Time Limit (minutes)"),
        null=True,
        blank=True,
        help_text=_("Optional time limit for completing the quiz."),
    )
    attempts_allowed = models.PositiveIntegerField(
        verbose_name=_("Attempts Allowed"),
        default=1,
        help_text=_("How many attempts a learner gets."),
    )
    shuffle_questions = models.BooleanField(
        verbose_name=_("Shuffle Questions"),
        default=False,
        help_text=_("Present questions in random order."),
    )
    randomize_options = models.BooleanField(
        verbose_name=_("Randomize Options"),
        default=False,
        help_text=_("Present answer options in random order."),
    )

    class Meta:
        verbose_name = _("Quiz")
        verbose_name_plural = _("Quizzes")
        ordering = ["title"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(
                        lesson__isnull=False, module__isnull=True, course__isnull=True
                    )
                    | models.Q(
                        lesson__isnull=True, module__isnull=False, course__isnull=True
                    )
                    | models.Q(
                        lesson__isnull=True, module__isnull=True, course__isnull=False
                    )
                ),
                name="quiz_exactly_one_parent",
            ),
            models.CheckConstraint(
                check=models.Q(passing_score__lte=100),
                name="quiz_passing_score_max_100",
            ),
        ]

    def __str__(self):
        """Label the quiz by level and title."""

        return f"{self.level}: {self.title}"


class Question(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A single question within a Quiz."""

    class TypeChoices(models.TextChoices):
        MULTIPLE_CHOICE = "MULTIPLE_CHOICE", "Multiple Choice"
        ESSAY = "ESSAY", "Essay"

    quiz = models.ForeignKey(
        "quizzes.Quiz",
        verbose_name=_("Quiz"),
        on_delete=models.CASCADE,
        related_name="questions",
    )
    question_text = models.TextField(
        verbose_name=_("Question Text"),
    )
    question_type = models.CharField(
        verbose_name=_("Type"),
        max_length=20,
        choices=TypeChoices.choices,
        help_text=_("Answer format; MULTIPLE_CHOICE requires options."),
    )
    points = models.PositiveIntegerField(
        verbose_name=_("Points"),
        default=10,
        help_text=_("Points awarded for a correct answer."),
    )
    model_response_guide = models.TextField(
        verbose_name=_("Model Response Guide"),
        blank=True,
        help_text=_("Reference answer used when grading ESSAY questions."),
    )
    order = models.PositiveIntegerField(
        verbose_name=_("Order"),
        default=0,
        help_text=_("Display position within the quiz."),
    )

    class Meta:
        verbose_name = _("Question")
        verbose_name_plural = _("Questions")
        ordering = ["order", "created_datetime"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(points__gt=0),
                name="question_points_positive",
            ),
            models.UniqueConstraint(
                fields=["quiz", "order"], name="unique_question_order_per_quiz"
            ),
        ]

    def __str__(self):
        """Use a truncated question text as the human-readable label."""

        return self.question_text[:50]


class QuestionOption(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One selectable answer for a MULTIPLE_CHOICE Question."""

    question = models.ForeignKey(
        "quizzes.Question",
        verbose_name=_("Question"),
        on_delete=models.CASCADE,
        related_name="options",
    )
    option_text = models.CharField(
        verbose_name=_("Option Text"),
        max_length=500,
    )
    explanation = models.TextField(
        verbose_name=_("Explanation"),
        blank=True,
        default="",
        help_text=_(
            "Why this option is right or wrong, shown after answering."
        ),
    )
    is_correct = models.BooleanField(
        verbose_name=_("Is Correct"),
        default=False,
        help_text=_("Whether selecting this option earns the question's points."),
    )
    order = models.PositiveIntegerField(
        verbose_name=_("Order"),
        default=0,
        help_text=_("Display position within the question; maps to the A/B/C/D letter."),
    )

    class Meta:
        verbose_name = _("Question Option")
        verbose_name_plural = _("Question Options")
        ordering = ["order", "created_datetime"]
        constraints = [
            models.UniqueConstraint(
                fields=["question", "order"], name="unique_option_order_per_question"
            ),
            # Exactly one correct option per multiple-choice question: this
            # partial unique index rejects a second is_correct=True row and
            # tolerates zero of them only while the question is still being
            # drafted. Mirrors the target schema's
            # idx_one_correct_option_per_question.
            models.UniqueConstraint(
                fields=["question"],
                condition=models.Q(is_correct=True),
                name="one_correct_option_per_question",
            ),
        ]

    def __str__(self):
        """Use truncated option text as the human-readable label."""

        return self.option_text[:40]
