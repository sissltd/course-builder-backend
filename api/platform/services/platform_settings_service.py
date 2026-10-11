from api.courses.enums import IN_FLIGHT_COURSE_STATUSES
from api.courses.models import Course
from api.platform.exceptions import StagedReviewFlowInFlight
from api.platform.models import PlatformSettings

UPDATABLE_FIELDS = {
    "minimum_withdrawal_threshold",
    "course_module_count_min",
    "course_module_count_max",
    "course_lessons_per_module_min",
    "course_lessons_per_module_max",
    "course_learning_objectives_min",
    "course_learning_objectives_max",
    "lesson_learning_objectives_min",
    "lesson_learning_objectives_max",
    "course_description_word_min",
    "course_description_word_max",
    "lesson_script_word_min",
    "lesson_script_word_max",
    "course_duration_min_minutes",
    "course_duration_max_minutes",
    "course_final_assessment_min_questions",
    "topic_reservation_expiry_days",
    "draft_minimum_hold_hours",
    "auto_flag_after_hours",
    "sla_amber_threshold_hours",
    "sla_red_threshold_hours",
    "payment_processor",
    "kyc_provider",
    "liveness_threshold",
    "auto_credit_duration_hours",
    "withdrawal_require_verification",
    "staged_review_flow_enabled",
    "production_enabled",
    "production_course_budget",
    "production_min_caption_accuracy",
    "production_max_av_drift_ms",
    "production_visual_check_enabled",
    "production_broll_per_lesson",
}


def get_settings() -> PlatformSettings:
    """Return the platform's single settings row, creating it with model
    defaults (which match the old env-var values) on first access."""

    settings_row = PlatformSettings.objects.first()
    if settings_row is None:
        settings_row = PlatformSettings.objects.create()
    return settings_row


def is_staged_review_flow_enabled() -> bool:
    """Whether courses are reviewed in the staged flow (text first, video
    second, resume at the rejecting seat). Off until an admin switches it on."""

    return get_settings().staged_review_flow_enabled


def update_settings(**fields) -> PlatformSettings:
    """Apply whichever settings fields were provided (all optional)."""

    settings_row = get_settings()
    update_fields = ["updated_datetime"]

    new_flow = fields.get("staged_review_flow_enabled")
    if (
        new_flow is not None
        and new_flow != settings_row.staged_review_flow_enabled
        and Course.objects.filter(status__in=IN_FLIGHT_COURSE_STATUSES).exists()
    ):
        raise StagedReviewFlowInFlight()

    for field, value in fields.items():
        if field in UPDATABLE_FIELDS and value is not None:
            setattr(settings_row, field, value)
            update_fields.append(field)

    if len(update_fields) > 1:
        settings_row.save(update_fields=update_fields)

    return settings_row
