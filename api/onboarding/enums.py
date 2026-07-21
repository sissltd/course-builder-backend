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


class ExpertiseArea(models.TextChoices):
    """Primary area of expertise selected during onboarding
    ("Which of these best describes your primary area of expertise?")."""

    WEB_DEVELOPMENT = "WEB_DEVELOPMENT", "Web Development"
    DATA_SCIENCE_ANALYTICS = "DATA_SCIENCE_ANALYTICS", "Data Science & Analytics"
    AI_MACHINE_LEARNING = "AI_MACHINE_LEARNING", "AI & Machine Learning"
    BUSINESS_MANAGEMENT = "BUSINESS_MANAGEMENT", "Business & Management"
    DIGITAL_MARKETING = "DIGITAL_MARKETING", "Digital Marketing"
    LEADERSHIP_SOFT_SKILLS = "LEADERSHIP_SOFT_SKILLS", "Leadership & Soft Skills"
    FINANCE_ACCOUNTING = "FINANCE_ACCOUNTING", "Finance & Accounting"
    OTHERS = "OTHERS", "Others (Specify)"
