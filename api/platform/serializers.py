from rest_framework import serializers

from api.platform.models import PlatformSettings


class PlatformSettingsSerializer(serializers.ModelSerializer):
    """Read-only representation of the platform's current settings."""

    class Meta:
        model = PlatformSettings
        fields = [
            "id",
            "minimum_withdrawal_threshold",
            "course_module_count_min",
            "course_module_count_max",
            "course_lessons_per_module_min",
            "course_lessons_per_module_max",
            "course_learning_objectives_min",
            "course_learning_objectives_max",
            "course_description_word_min",
            "course_description_word_max",
            "lesson_script_word_min",
            "lesson_script_word_max",
            "lesson_quiz_questions_min",
            "lesson_quiz_questions_max",
            "course_duration_min_minutes",
            "course_duration_max_minutes",
            "course_final_assessment_min_questions",
            "topic_reservation_expiry_days",
            "updated_datetime",
        ]
        read_only_fields = fields


class PlatformSettingsUpdateSerializer(serializers.Serializer):
    """Write serializer for PATCH /platform-settings/. All fields optional
    so an Admin/Super Admin can tune just one knob at a time."""

    minimum_withdrawal_threshold = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False
    )
    course_module_count_min = serializers.IntegerField(required=False, min_value=1)
    course_module_count_max = serializers.IntegerField(required=False, min_value=1)
    course_lessons_per_module_min = serializers.IntegerField(
        required=False, min_value=1
    )
    course_lessons_per_module_max = serializers.IntegerField(
        required=False, min_value=1
    )
    course_learning_objectives_min = serializers.IntegerField(
        required=False, min_value=1
    )
    course_learning_objectives_max = serializers.IntegerField(
        required=False, min_value=1
    )
    course_description_word_min = serializers.IntegerField(required=False, min_value=1)
    course_description_word_max = serializers.IntegerField(required=False, min_value=1)
    lesson_script_word_min = serializers.IntegerField(required=False, min_value=1)
    lesson_script_word_max = serializers.IntegerField(required=False, min_value=1)
    lesson_quiz_questions_min = serializers.IntegerField(required=False, min_value=1)
    lesson_quiz_questions_max = serializers.IntegerField(required=False, min_value=1)
    course_duration_min_minutes = serializers.IntegerField(required=False, min_value=1)
    course_duration_max_minutes = serializers.IntegerField(required=False, min_value=1)
    course_final_assessment_min_questions = serializers.IntegerField(
        required=False, min_value=1
    )
    topic_reservation_expiry_days = serializers.IntegerField(
        required=False, min_value=1
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "At least one platform setting must be provided."
            )
        return attrs
