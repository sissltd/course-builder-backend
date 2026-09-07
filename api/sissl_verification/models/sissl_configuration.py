from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from core.mixins import (
    DateHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


# >>>>>>>>>>>>>>>>>>>>> SISSL Configuration <<<<<<<<<<<<<<<<<<<<<<<<<<
class SISSLConfiguration(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, models.Model):
    """
    Singleton operational config — admin-editable WITHOUT a redeploy.

    Holds runtime-tunable thresholds and HTTP knobs for SISSL. Env vars
    (shared/constants/kyc.py) act as fallbacks if a particular field is
    not set on the singleton row.

    NOTE: There is NO verification_mode / kill switch.
    Verification is automatic — if SISSL fails, the user does not proceed.
    Do NOT reintroduce a bypass mode without a product decision.
    """

    # Constant used to identify the one (and only) row
    SINGLETON_KEY = "sissl"


    # >>>>>>>>>>>>>>>>>>>> Singleton Guard <<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    # `unique=True` + a hardcoded default makes it impossible to create
    # a second row by accident — Django will raise IntegrityError.
    singleton_key = models.CharField(
        max_length=16,
        unique=True,
        default=SINGLETON_KEY,
        editable=False,
        help_text="Internal — do not edit. Enforces singleton (one row only).",
    )


    # >>>>>>>>>>>>>>>>>>>> Score Thresholds <<<<<<<<<<<<<<<<<<<<<<<<<<<<
    """
    All three scores are on a 0 - 100 scale (returned by SISSL).

    face_match_threshold  -> at or above this, face comparison = PASS
    flagging_floor        -> at or above this (but below face_match_threshold),
                             face comparison = FLAG for officer review
    liveness_threshold    -> at or above this AND result=="real", liveness passes

    Anything below flagging_floor on face comparison is a hard FAIL.
    """

    face_match_threshold = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Score (0 - 100) at or above which a face comparison passes",
    )

    flagging_floor = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Score floor for flagging — below this, the comparison hard-fails",
    )

    liveness_threshold = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Score (0 - 100) at or above which a 'real' liveness result passes",
    )


    # >>>>>>>>>>>>>>>>>>>> HTTP Knobs <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    # Max number of retry attempts the provider will make on transient 5xx
    retry_max_attempts = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text="Max provider retry attempts on transient 5xx (502/503/504)",
    )

    # Hard timeout per single HTTP call (seconds)
    http_timeout_seconds = models.PositiveSmallIntegerField(
        default=30,
        validators=[MinValueValidator(1), MaxValueValidator(120)],
        help_text="Hard timeout per SISSL HTTP call, in seconds",
    )


    class Meta:
        db_table = "sissl_configurations"
        verbose_name = "SISSL Configuration"
        verbose_name_plural = "SISSL Configuration"


    # >>>>>>>>>>>>>>>>>>>> Singleton Accessor <<<<<<<<<<<<<<<<<<<<<<<<<<
    @classmethod
    def current(cls):
        """
        Returns the singleton row, or None if it has not been seeded yet.

        Callers in services/ should treat a None return as "use env-var defaults"
        rather than crashing — that way the app degrades gracefully if the
        singleton hasn't been created yet.
        """
        return cls.objects.filter(singleton_key=cls.SINGLETON_KEY).first()


    def __str__(self):
        return (
            f"SISSLConfiguration :: face={self.face_match_threshold} | "
            f"flag={self.flagging_floor} | live={self.liveness_threshold} | "
            f"timeout={self.http_timeout_seconds}s"
        )
