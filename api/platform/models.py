from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from api.platform.enums import PaymentProcessors
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class PlatformSettings(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """Singleton, admin-editable platform-wide configuration.

    Replaces the env-var Django settings that used to live in
    config/settings/courses.py - course_validation_service and wallet_service
    now read these values from the DB instead, via
    platform_settings_service.get_settings(), so an Admin/Super Admin can
    tune them without a deploy. Exactly one row is ever created; there's no
    API path that creates a second (get_settings() always operates on the
    first row, creating it with these model defaults on first access).
    """

    minimum_withdrawal_threshold = models.DecimalField(
        verbose_name=_("Minimum Withdrawal Threshold"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("50.00"),
        help_text=_("Minimum amount a creator can request as a withdrawal."),
    )
    course_module_count_min = models.PositiveIntegerField(
        verbose_name=_("Course Module Count Min"), default=4
    )
    course_module_count_max = models.PositiveIntegerField(
        verbose_name=_("Course Module Count Max"), default=12
    )
    course_lessons_per_module_min = models.PositiveIntegerField(
        verbose_name=_("Course Lessons Per Module Min"), default=3
    )
    course_lessons_per_module_max = models.PositiveIntegerField(
        verbose_name=_("Course Lessons Per Module Max"), default=8
    )
    course_learning_objectives_min = models.PositiveIntegerField(
        verbose_name=_("Course Learning Objectives Min"), default=5
    )
    course_learning_objectives_max = models.PositiveIntegerField(
        verbose_name=_("Course Learning Objectives Max"), default=5
    )
    lesson_learning_objectives_min = models.PositiveIntegerField(
        verbose_name=_("Lesson Learning Objectives Min"), default=2
    )
    lesson_learning_objectives_max = models.PositiveIntegerField(
        verbose_name=_("Lesson Learning Objectives Max"), default=5
    )
    course_description_word_min = models.PositiveIntegerField(
        verbose_name=_("Course Description Word Min"), default=100
    )
    course_description_word_max = models.PositiveIntegerField(
        verbose_name=_("Course Description Word Max"), default=500
    )
    lesson_script_word_min = models.PositiveIntegerField(
        verbose_name=_("Lesson Script Word Min"), default=500
    )
    lesson_script_word_max = models.PositiveIntegerField(
        verbose_name=_("Lesson Script Word Max"), default=1500
    )
    course_duration_min_minutes = models.PositiveIntegerField(
        verbose_name=_("Course Duration Min Minutes"), default=120
    )
    course_duration_max_minutes = models.PositiveIntegerField(
        verbose_name=_("Course Duration Max Minutes"), default=480
    )
    course_final_assessment_min_questions = models.PositiveIntegerField(
        verbose_name=_("Course Final Assessment Min Questions"), default=15
    )
    topic_reservation_expiry_days = models.PositiveIntegerField(
        verbose_name=_("Topic Reservation Expiry Days"),
        default=30,
        help_text=_("How long an approved topic reservation lasts (BR-007)."),
    )
    sla_amber_threshold_hours = models.PositiveIntegerField(
        verbose_name=_("SLA Amber Threshold Hours"),
        default=24,
        help_text=_(
            "Hours since submission before a queued course is flagged amber. "
            "Platform-wide default; a reviewer may override it for themselves "
            "via NotificationPreference."
        ),
    )
    sla_red_threshold_hours = models.PositiveIntegerField(
        verbose_name=_("SLA Red Threshold Hours"),
        default=48,
        help_text=_(
            "Hours since submission before a queued course is flagged red/"
            "critical. Platform-wide default; a reviewer may override it for "
            "themselves via NotificationPreference."
        ),
    )
    mfa_enrollment_grace_period_days = models.PositiveIntegerField(
        verbose_name=_("MFA Enrollment Grace Period Days"),
        default=7,
        help_text=_(
            "How many days an Admin/Super Admin may keep logging in without "
            "MFA enrolled before enforcement on sensitive actions kicks in. "
            "The clock starts once, at role assignment - not reset by "
            "re-saving this setting."
        ),
    )
    payment_processor = models.CharField(
        verbose_name=_("Payment Processor"),
        max_length=20,
        choices=PaymentProcessors.choices,
        default=PaymentProcessors.FLUTTERWAVE,
        help_text=_("Which payment processor to use for creator payouts."),
    )

    class Meta:
        verbose_name = _("Platform Settings")
        verbose_name_plural = _("Platform Settings")

    def __str__(self):
        """Single row, so a fixed label is more useful than any field value."""

        return "Platform Settings"
