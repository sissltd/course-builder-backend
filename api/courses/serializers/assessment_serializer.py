from rest_framework import serializers

from api.courses.enums import QuestionType
from api.courses.models import Assessment


class QuizOptionSerializer(serializers.Serializer):
    """One answer option on a MULTIPLE_CHOICE question.

    `explanation` is required on every option (SCCS PRD Section 6.3: "every
    question must have explanations for both correct and incorrect
    answers") - not just the correct one.
    """

    text = serializers.CharField(allow_blank=False)
    explanation = serializers.CharField(allow_blank=False)


class QuizQuestionSerializer(serializers.Serializer):
    """One quiz question, shaped differently depending on `type`.

    MULTIPLE_CHOICE requires `options` (>=2, each with its own explanation)
    and a `correct_index` into them. ESSAY requires `explanation` instead
    (the model-answer/grading guidance shown to the reviewer) and carries no
    options - the two shapes are mutually exclusive so a client can't send a
    half-multiple-choice, half-essay question.
    """

    type = serializers.ChoiceField(
        choices=QuestionType.choices, default=QuestionType.MULTIPLE_CHOICE
    )
    question = serializers.CharField(allow_blank=False)
    points = serializers.IntegerField(min_value=0, default=0)
    options = QuizOptionSerializer(many=True, required=False)
    correct_index = serializers.IntegerField(required=False)
    explanation = serializers.CharField(required=False, allow_blank=False)

    def validate(self, attrs):
        question_type = attrs.get("type", QuestionType.MULTIPLE_CHOICE)

        if question_type == QuestionType.MULTIPLE_CHOICE:
            if "explanation" in attrs:
                raise serializers.ValidationError(
                    {
                        "explanation": (
                            "Multiple-choice questions carry per-option "
                            "explanations, not a top-level one."
                        )
                    }
                )
            options = attrs.get("options")
            if not options or len(options) < 2:
                raise serializers.ValidationError(
                    {"options": "Multiple-choice questions need at least 2 options."}
                )
            correct_index = attrs.get("correct_index")
            if not isinstance(correct_index, int) or not (
                0 <= correct_index < len(options)
            ):
                raise serializers.ValidationError(
                    {"correct_index": "Must be a valid index into 'options'."}
                )
        else:  # ESSAY
            if (
                attrs.get("options") is not None
                or attrs.get("correct_index") is not None
            ):
                raise serializers.ValidationError(
                    {
                        "options": (
                            "Essay questions don't take options or a correct_index."
                        )
                    }
                )
            if not attrs.get("explanation"):
                raise serializers.ValidationError(
                    {"explanation": "Essay questions require an explanation."}
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
    explanations). Count-per-level thresholds (e.g. 3-5 questions per
    lesson, >=15 for the final assessment) are intentionally centralized in
    course_validation_service and enforced at submit time, not duplicated
    here.
    """

    questions = QuizQuestionSerializer(many=True)

    class Meta:
        model = Assessment
        fields = ["title", "questions"]
