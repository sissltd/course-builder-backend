from rest_framework import serializers

from api.courses.enums import QuestionType
from api.courses.models import Assessment

MINIMUM_CHOICE_OPTIONS = 2
MAXIMUM_CHOICE_OPTIONS = 6


class QuizOptionSerializer(serializers.Serializer):
    """One answer option on a choice question.

    `explanation` is required on every option (SCCS PRD Section 6.3: "every
    question must have explanations for both correct and incorrect
    answers") - not just the correct one.
    """

    text = serializers.CharField(allow_blank=False)
    explanation = serializers.CharField(allow_blank=False)


class QuizQuestionSerializer(serializers.Serializer):
    """One quiz question, shaped differently depending on `type`.

    SINGLE_CHOICE has one correct option; MULTIPLE_CHOICE has one or more.
    Both have 2-6 options and an explanation for each option. ESSAY has
    distinct top-level expected_answer and explanation fields, and no options.
    The legacy
    MULTIPLE_CHOICE + correct_index shape remains valid for existing clients.
    """

    type = serializers.ChoiceField(
        choices=QuestionType.choices, default=QuestionType.MULTIPLE_CHOICE
    )
    question = serializers.CharField(allow_blank=False)
    points = serializers.IntegerField(min_value=0, default=0)
    options = QuizOptionSerializer(many=True, required=False)
    correct_index = serializers.IntegerField(required=False)
    correct_indices = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=False
    )
    expected_answer = serializers.CharField(required=False, allow_blank=False)
    explanation = serializers.CharField(required=False, allow_blank=False)

    def validate(self, attrs):
        question_type = attrs.get("type", QuestionType.MULTIPLE_CHOICE)

        if question_type in {
            QuestionType.SINGLE_CHOICE,
            QuestionType.MULTIPLE_CHOICE,
        }:
            if "expected_answer" in attrs or "explanation" in attrs:
                raise serializers.ValidationError(
                    {
                        "expected_answer": (
                            "Choice questions carry per-option "
                            "explanations, not top-level expected answers or "
                            "explanations."
                        )
                    }
                )
            options = attrs.get("options")
            if not options or len(options) < MINIMUM_CHOICE_OPTIONS:
                raise serializers.ValidationError(
                    {
                        "options": (
                            "Choice questions need at least "
                            f"{MINIMUM_CHOICE_OPTIONS} options."
                        )
                    }
                )
            if len(options) > MAXIMUM_CHOICE_OPTIONS:
                raise serializers.ValidationError(
                    {
                        "options": (
                            "Choice questions cannot have more than "
                            f"{MAXIMUM_CHOICE_OPTIONS} options."
                        )
                    }
                )

            uses_legacy_single_choice = (
                question_type == QuestionType.MULTIPLE_CHOICE
                and "correct_index" in attrs
                and "correct_indices" not in attrs
            )
            if (
                question_type == QuestionType.SINGLE_CHOICE
                or uses_legacy_single_choice
            ):
                if "correct_indices" in attrs:
                    raise serializers.ValidationError(
                        {
                            "correct_indices": (
                                "Single-choice questions use correct_index."
                            )
                        }
                    )
                correct_index = attrs.get("correct_index")
                if not isinstance(correct_index, int) or not (
                    0 <= correct_index < len(options)
                ):
                    raise serializers.ValidationError(
                        {"correct_index": "Must be a valid index into 'options'."}
                    )
            else:
                if "correct_index" in attrs:
                    raise serializers.ValidationError(
                        {
                            "correct_index": (
                                "Multiple-choice questions use correct_indices."
                            )
                        }
                    )
                correct_indices = attrs.get("correct_indices")
                if not correct_indices:
                    raise serializers.ValidationError(
                        {
                            "correct_indices": (
                                "Select at least one correct option."
                            )
                        }
                    )
                if len(correct_indices) != len(set(correct_indices)) or any(
                    index < 0 or index >= len(options) for index in correct_indices
                ):
                    raise serializers.ValidationError(
                        {
                            "correct_indices": (
                                "Must contain unique valid indexes into 'options'."
                            )
                        }
                    )
        else:  # ESSAY
            if (
                attrs.get("options") is not None
                or attrs.get("correct_index") is not None
                or attrs.get("correct_indices") is not None
            ):
                raise serializers.ValidationError(
                    {
                        "options": (
                            "Essay questions don't take options or correct-answer indexes."
                        )
                    }
                )
            if not attrs.get("explanation"):
                raise serializers.ValidationError(
                    {"explanation": "Essay questions require an explanation."}
                )
            if not attrs.get("expected_answer"):
                raise serializers.ValidationError(
                    {"expected_answer": "Essay questions require an expected answer."}
                )

        return attrs


class AssessmentSerializer(serializers.ModelSerializer):
    """Read-only representation of an Assessment (quiz)."""

    questions = QuizQuestionSerializer(many=True, read_only=True)
    summary = serializers.SerializerMethodField()

    class Meta:
        model = Assessment
        fields = ["id", "level", "title", "questions", "summary"]
        # questions/summary are already read-only via their own field
        # declarations above; listing them here too would trip DRF's
        # "can't declare a field AND put it in read_only_fields" assertion.
        read_only_fields = ["id", "level", "title"]

    def get_summary(self, obj) -> dict:
        """Aggregate counts backing the Quiz Builder's "Quiz summary" panel.

        Computed on read from `questions` rather than stored, so it can
        never drift out of sync with the questions actually saved - same
        pattern as course_validation_service.get_course_duration_minutes
        and wallet_service.get_wallet_totals.
        """

        questions = obj.questions or []
        return {
            "total_questions": len(questions),
            "total_points": sum(question.get("points", 0) for question in questions),
            "single_choice_count": sum(
                1
                for question in questions
                if question.get("type") == QuestionType.SINGLE_CHOICE
            ),
            "multiple_choice_count": sum(
                1
                for question in questions
                if question.get("type", QuestionType.MULTIPLE_CHOICE)
                == QuestionType.MULTIPLE_CHOICE
            ),
            "essay_count": sum(
                1
                for question in questions
                if question.get("type") == QuestionType.ESSAY
            ),
        }


class AssessmentWriteSerializer(serializers.ModelSerializer):
    """Create/update serializer for an Assessment.

    `questions` is validated per-item by QuizQuestionSerializer (shape only:
    field presence, option count, correct_index range, required
    explanations). Lesson assessments are optional and have no question-count
    threshold. Submission-time rules for module-assessment presence and the
    final-assessment minimum remain centralized in quality_check_service.
    """

    questions = QuizQuestionSerializer(many=True)

    class Meta:
        model = Assessment
        fields = ["title", "questions"]
