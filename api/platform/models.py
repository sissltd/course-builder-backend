from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from api.platform.enums import KYCProvider, PaymentProcessors
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
        verbose_name=_("Course Learning Objectives Max"), default=10
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
        verbose_name=_("Course Duration Min Minutes"),
        default=30,
        help_text=_("Shortest total runtime, in minutes, a course may have."),
    )
    course_duration_max_minutes = models.PositiveIntegerField(
        verbose_name=_("Course Duration Max Minutes"),
        default=480,
        help_text=_(
            "Longest total runtime, in minutes, a course may have. There is no "
            "hard ceiling: an admin may raise it to any length."
        ),
    )
    course_final_assessment_min_questions = models.PositiveIntegerField(
        verbose_name=_("Course Final Assessment Min Questions"), default=15
    )
    topic_reservation_expiry_days = models.PositiveIntegerField(
        verbose_name=_("Topic Reservation Expiry Days"),
        default=30,
        help_text=_("How long an approved topic reservation lasts (BR-007)."),
    )
    draft_minimum_hold_hours = models.PositiveIntegerField(
        verbose_name=_("Draft Minimum Hold Hours"),
        default=48,
        help_text=_(
            "Hours a course must exist as a draft before its creator may "
            "submit it. Counted from when the course was first created and "
            "never reset, so a course returned for revision can be "
            "resubmitted at once. 0 disables the rule."
        ),
    )
    auto_flag_after_hours = models.PositiveIntegerField(
        verbose_name=_("Auto Flag After Hours"),
        default=48,
        help_text=_(
            "Hours a course may await a review decision before it is flagged "
            "for admin attention. The flag is advisory - it blocks nothing - "
            "and clears when the course next enters a review cycle. 0 "
            "disables flagging."
        ),
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
    payment_processor = models.CharField(
        verbose_name=_("Payment Processor"),
        max_length=20,
        choices=PaymentProcessors.choices,
        default=PaymentProcessors.FLUTTERWAVE,
        help_text=_("Which payment processor to use for creator payouts."),
    )
    kyc_provider = models.CharField(
        verbose_name=_("KYC service provider"),
        max_length=20,
        choices=KYCProvider.choices,
        default=KYCProvider.SISSL,
        help_text=_("Which KYC service provider to use for identity verification."),
    )
    liveness_threshold = models.PositiveSmallIntegerField(
        verbose_name=_("Liveness Threshold"),
        help_text=_("Score (0 - 100) at or above which a 'real' liveness result passes"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        blank=True,
        default=80,
    )
    auto_credit_duration_hours = models.PositiveIntegerField(
        verbose_name=_("Auto Credit Duration Hours"),
        default=24,
        help_text=_(
            "Number of hours after course approval before the creator's wallet is credited."
        ),
    )
    withdrawal_require_verification = models.BooleanField(
        verbose_name=_("Withdrawal Requires Verification"),
        default=True,
        help_text=_(
            "Whether creators must complete identity verification before making withdrawals."
        ),
    )
    staged_review_flow_enabled = models.BooleanField(
        verbose_name=_("Staged Review Flow Enabled"),
        default=False,
        help_text=_(
            "Switches course review to the staged flow: courses are "
            "submitted as text only, the first seat reviews the text, the "
            "video is attached afterwards and reviewed at the second seat, "
            "and a rejection returns the course to Needs Revision and "
            "resumes at the rejecting seat. Only the Approver may price and "
            "publish. Off keeps the original single-pass review. Cannot be "
            "changed while any course is in review."
        ),
    )
    production_enabled = models.BooleanField(
        verbose_name=_("Production Engine Enabled"),
        default=False,
        help_text=_(
            "Kill switch for the Production Engine. Off: runs are still "
            "created and quoted for courses waiting on engine-made video, but "
            "none starts and a running one stops before its next step."
        ),
    )
    production_course_budget = models.DecimalField(
        verbose_name=_("Production Budget Per Course"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("100.00"),
        help_text=_(
            "Most the Production Engine may spend producing one course, in "
            "USD. A run whose quote is higher is blocked before any spend, "
            "and a run stops if its actual spend reaches it."
        ),
    )
    production_min_caption_accuracy = models.DecimalField(
        verbose_name=_("Production Minimum Caption Accuracy"),
        max_digits=5,
        decimal_places=2,
        default=Decimal("95.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text=_(
            "Lowest caption accuracy (percent of words a transcription of the "
            "finished audio gets right against the approved script) a "
            "produced lesson may have before it goes to review."
        ),
    )
    production_max_av_drift_ms = models.PositiveIntegerField(
        verbose_name=_("Production Maximum A/V Drift (ms)"),
        default=100,
        help_text=_(
            "Largest gap between the audio and video track lengths of a "
            "produced lesson, in milliseconds."
        ),
    )
    production_visual_check_enabled = models.BooleanField(
        verbose_name=_("Production Visual Check"),
        default=True,
        help_text=_(
            "Have a vision model look at a frame of every scene of a produced "
            "lesson (text legible and spelled right, nothing cut off, no people "
            "or faces) before it goes to review. About a cent per lesson."
        ),
    )
    production_broll_per_lesson = models.PositiveSmallIntegerField(
        verbose_name=_("Production B-roll Clips Per Lesson"),
        default=0,
        validators=[MaxValueValidator(5)],
        help_text=_(
            "Most AI motion clips (8 s, faceless) a produced lesson may use in "
            "place of a still scene. 0 turns b-roll off. Each clip costs about "
            "$1.20 and is included in the quote."
        ),
    )

    class Meta:
        verbose_name = _("Platform Settings")
        verbose_name_plural = _("Platform Settings")

    def __str__(self):
        """Single row, so a fixed label is more useful than any field value."""

        return "Platform Settings"
