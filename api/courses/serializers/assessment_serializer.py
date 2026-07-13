from rest_framework import serializers

from api.courses.models import Assessment


class AssessmentSerializer(serializers.ModelSerializer):
    """Read-only representation of an Assessment (quiz)."""

    class Meta:
        model = Assessment
        fields = ["id", "level", "title", "questions"]
        read_only_fields = fields


class AssessmentWriteSerializer(serializers.ModelSerializer):
    """Create/update serializer for an Assessment.

    Validates only the *shape* of `questions` (each item has a question
    string, at least 2 options, and a valid correct_index). Count-per-level
    thresholds (e.g. 3-5 questions per lesson, >=15 for the final assessment)
    are intentionally centralized in course_validation_service and enforced
    at submit time, not duplicated here.
    """

    class Meta:
        model = Assessment
        fields = ["title", "questions"]

    def validate_questions(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("questions must be a list.")

        for index, question in enumerate(value):
            if not isinstance(question, dict):
                raise serializers.ValidationError(f"Question {index} must be an object.")

            text = question.get("question")
            options = question.get("options")
            correct_index = question.get("correct_index")

            if not isinstance(text, str) or not text.strip():
                raise serializers.ValidationError(f"Question {index} is missing 'question' text.")
            if not isinstance(options, list) or len(options) < 2:
                raise serializers.ValidationError(
                    f"Question {index} must have at least 2 'options'."
                )
            if not all(isinstance(option, str) for option in options):
                raise serializers.ValidationError(f"Question {index} 'options' must all be strings.")
            if not isinstance(correct_index, int) or not (0 <= correct_index < len(options)):
                raise serializers.ValidationError(
                    f"Question {index} 'correct_index' must be a valid index into 'options'."
                )

        return value
