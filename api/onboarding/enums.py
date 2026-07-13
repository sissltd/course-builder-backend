from django.db import models


class VideoComfortLevel(models.TextChoices):
    """How comfortable a creator is producing video content, collected during
    onboarding ("How comfortable are you creating video content?")."""

    NEEDS_GUIDANCE = "NEEDS_GUIDANCE", "Not comfortable, I'll need guidance"
    SOMEWHAT_COMFORTABLE = (
        "SOMEWHAT_COMFORTABLE",
        "Somewhat comfortable, I've recorded a few videos",
    )
    VERY_COMFORTABLE = "VERY_COMFORTABLE", "Very comfortable, I produce video regularly"
    PREFERS_TEXT_AUDIO = "PREFERS_TEXT_AUDIO", "I prefer text-based or audio content"


class MonthlyCourseCapacity(models.TextChoices):
    """How many courses a creator estimates they can produce per month,
    collected during onboarding."""

    ONE = "ONE", "1 course"
    TWO_TO_THREE = "TWO_TO_THREE", "2-3 courses"
    FOUR_TO_FIVE = "FOUR_TO_FIVE", "4-5 courses"
    MORE_THAN_FIVE = "MORE_THAN_FIVE", "More than 5 courses"
