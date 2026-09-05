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
    "lesson_quiz_questions_min",
    "lesson_quiz_questions_max",
    "course_duration_min_minutes",
    "course_duration_max_minutes",
    "course_final_assessment_min_questions",
    "topic_reservation_expiry_days",
    "sla_amber_threshold_hours",
    "sla_red_threshold_hours",
    "mfa_enrollment_grace_period_days",
    "payment_processor",
    "kyc_provider",
}


def get_settings() -> PlatformSettings:
    """Return the platform's single settings row, creating it with model
    defaults (which match the old env-var values) on first access."""

    settings_row = PlatformSettings.objects.first()
    if settings_row is None:
        settings_row = PlatformSettings.objects.create()
    return settings_row


def update_settings(**fields) -> PlatformSettings:
    """Apply whichever settings fields were provided (all optional)."""

    settings_row = get_settings()
    update_fields = ["updated_datetime"]

    for field, value in fields.items():
        if field in UPDATABLE_FIELDS and value is not None:
            setattr(settings_row, field, value)
            update_fields.append(field)

    if len(update_fields) > 1:
        settings_row.save(update_fields=update_fields)

    return settings_row
