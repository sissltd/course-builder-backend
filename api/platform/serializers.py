from rest_framework import serializers

from api.platform.enums import PaymentProcessors
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
            "sla_amber_threshold_hours",
            "sla_red_threshold_hours",
            "mfa_enrollment_grace_period_days",
            "updated_datetime",
            "payment_processor",
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
    sla_amber_threshold_hours = serializers.IntegerField(required=False, min_value=1)
    sla_red_threshold_hours = serializers.IntegerField(required=False, min_value=1)
    mfa_enrollment_grace_period_days = serializers.IntegerField(
        required=False, min_value=0
    )
    payment_processor = serializers.ChoiceField(
        required=False,
        choices=PaymentProcessors.choices,
        help_text="Which payment processor to use for creator payouts.",
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


class CreatorOverviewWalletSerializer(serializers.Serializer):
    """The signed-in creator's own wallet figures on their overview."""

    balance = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Current wallet balance.",
    )
    currency = serializers.CharField(
        help_text="ISO 4217 currency code of the wallet.",
    )
    total_earned = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Lifetime total credited to this wallet via approved courses.",
    )
    pending_balance = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text=(
            "Sum of pending withdrawal debits - already deducted from the "
            "balance, not yet settled."
        ),
    )


class CreatorOverviewSerializer(serializers.Serializer):
    """Counts and totals backing a creator's home screen.

    Mirrors AdminOverviewSerializer's conventions: counts only (each figure
    has its own endpoint behind it), every CourseStatus key always present
    including zeroes, computed live per request.
    """

    courses = serializers.DictField(
        child=serializers.IntegerField(),
        help_text="Counts of the creator's own courses keyed by CourseStatus.",
    )
    wallet = CreatorOverviewWalletSerializer(
        help_text="The creator's wallet balance and lifetime/pending money figures."
    )
    pending_invites = serializers.IntegerField(
        help_text=(
            "Collaboration invites addressed to this account's email that "
            "are still pending and unexpired."
        )
    )


class ReviewerMyDecisionsSerializer(serializers.Serializer):
    """A reviewer's own decision counts."""

    approved = serializers.IntegerField(
        help_text="Lifetime count of courses this reviewer has approved.",
    )
    today = serializers.IntegerField(
        help_text="Decisions this reviewer has recorded since local midnight.",
    )


class ReviewerOverviewSerializer(serializers.Serializer):
    """Counts backing a reviewer's home screen.

    `queue` is keyed by the two reviewable CourseStatus values and always
    contains both, so tiles are stable. `my_decisions` summarizes the
    reviewer's own ReviewAction history.
    """

    queue = serializers.DictField(
        child=serializers.IntegerField(),
        help_text="Courses awaiting review keyed by CourseStatus (SUBMITTED, IN_REVIEW).",
    )
    my_decisions = ReviewerMyDecisionsSerializer(
        help_text="The reviewer's own approve/reject history summary."
    )


class TestEmailSerializer(serializers.Serializer):
    """Optional overrides for the `POST /api/v1/test-email/` smoke test.

    All fields are optional so the endpoint still works with an empty body,
    and the recipient defaults to the authenticated user's own address.
    """

    email = serializers.EmailField(
        required=False,
        allow_blank=True,
        help_text=(
            "Recipient address for the test message. Defaults to the "
            "authenticated user's email when omitted."
        ),
    )
    subject = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=150,
        help_text="Subject line for the test message. Defaults to a standard probe subject.",
    )
    message = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
        help_text=(
            "Plain-text body of the test message. Defaults to a standard "
            "probe message when omitted."
        ),
    )
