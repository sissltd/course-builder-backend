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
        max_digits=10,
        decimal_places=2,
        required=False,
        help_text=(
            "Smallest amount a creator may request as a withdrawal, in the "
            "wallet's currency. Omit to leave unchanged."
        ),
    )
    course_module_count_min = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Fewest modules a course needs to pass submission validation.",
    )
    course_module_count_max = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Most modules a course may have at submission.",
    )
    course_lessons_per_module_min = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Fewest lessons each module needs at submission.",
    )
    course_lessons_per_module_max = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Most lessons each module may have at submission.",
    )
    course_learning_objectives_min = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Fewest learning objectives a course must declare.",
    )
    course_learning_objectives_max = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Most learning objectives a course may declare.",
    )
    course_description_word_min = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Fewest words allowed in a course description.",
    )
    course_description_word_max = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Most words allowed in a course description.",
    )
    lesson_script_word_min = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Fewest words allowed in a lesson script.",
    )
    lesson_script_word_max = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Most words allowed in a lesson script.",
    )
    lesson_quiz_questions_min = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Fewest questions a lesson quiz must contain.",
    )
    lesson_quiz_questions_max = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Most questions a lesson quiz may contain.",
    )
    course_duration_min_minutes = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Shortest total runtime, in minutes, a course may have.",
    )
    course_duration_max_minutes = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Longest total runtime, in minutes, a course may have.",
    )
    course_final_assessment_min_questions = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Fewest questions a course's final assessment must contain.",
    )
    topic_reservation_expiry_days = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text=(
            "Days a creator's topic reservation is held before it lapses and "
            "the topic returns to the pool."
        ),
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "At least one platform setting must be provided."
            )
        return attrs


class AdminOverviewWalletTotalsSerializer(serializers.Serializer):
    """Platform-wide money figures on the admin overview."""

    balance_held = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Sum of every creator wallet balance the platform currently holds.",
    )
    total_credited = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Lifetime total credited to creators via approved courses.",
    )
    awaiting_payout = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text=(
            "Sum of confirmed withdrawal debits still pending settlement. Already "
            "deducted from wallet balances, not yet paid out."
        ),
    )


class AdminOverviewSerializer(serializers.Serializer):
    """Counts and totals backing the admin home screen.

    Each count block is keyed by the corresponding status enum value and always
    contains every value, including zeroes, so the dashboard's tiles do not
    appear and disappear with the data.
    """

    users = serializers.DictField(
        child=serializers.IntegerField(),
        help_text="User account counts keyed by AccountStatus.",
    )
    courses = serializers.DictField(
        child=serializers.IntegerField(),
        help_text="Course counts keyed by CourseStatus.",
    )
    kyc = serializers.DictField(
        child=serializers.IntegerField(),
        help_text="KYC submission counts keyed by KYCStatus.",
    )
    withdrawals = serializers.DictField(
        child=serializers.IntegerField(),
        help_text="Withdrawal request counts keyed by WithdrawalRequestStatus.",
    )
    wallet_totals = AdminOverviewWalletTotalsSerializer(
        help_text="Platform-wide wallet money figures."
    )
