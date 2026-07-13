from decimal import Decimal

from decouple import config

# Wallet
MINIMUM_WITHDRAWAL_THRESHOLD = config(
    "MINIMUM_WITHDRAWAL_THRESHOLD", default="50.00", cast=Decimal
)

# Course structural quality standards (SCCS PRD v3.5 Section 6.1-6.3)
COURSE_MODULE_COUNT_MIN = config("COURSE_MODULE_COUNT_MIN", default=4, cast=int)
COURSE_MODULE_COUNT_MAX = config("COURSE_MODULE_COUNT_MAX", default=12, cast=int)

COURSE_LESSONS_PER_MODULE_MIN = config(
    "COURSE_LESSONS_PER_MODULE_MIN", default=3, cast=int
)
COURSE_LESSONS_PER_MODULE_MAX = config(
    "COURSE_LESSONS_PER_MODULE_MAX", default=8, cast=int
)

COURSE_LEARNING_OBJECTIVES_MIN = config(
    "COURSE_LEARNING_OBJECTIVES_MIN", default=2, cast=int
)
COURSE_LEARNING_OBJECTIVES_MAX = config(
    "COURSE_LEARNING_OBJECTIVES_MAX", default=5, cast=int
)

COURSE_DURATION_MIN_MINUTES = config(
    "COURSE_DURATION_MIN_MINUTES", default=120, cast=int
)
COURSE_DURATION_MAX_MINUTES = config(
    "COURSE_DURATION_MAX_MINUTES", default=480, cast=int
)

COURSE_DESCRIPTION_WORD_MIN = config(
    "COURSE_DESCRIPTION_WORD_MIN", default=100, cast=int
)
COURSE_DESCRIPTION_WORD_MAX = config(
    "COURSE_DESCRIPTION_WORD_MAX", default=500, cast=int
)

LESSON_SCRIPT_WORD_MIN = config("LESSON_SCRIPT_WORD_MIN", default=500, cast=int)
LESSON_SCRIPT_WORD_MAX = config("LESSON_SCRIPT_WORD_MAX", default=1500, cast=int)

LESSON_QUIZ_QUESTIONS_MIN = config("LESSON_QUIZ_QUESTIONS_MIN", default=3, cast=int)
LESSON_QUIZ_QUESTIONS_MAX = config("LESSON_QUIZ_QUESTIONS_MAX", default=5, cast=int)

COURSE_FINAL_ASSESSMENT_MIN_QUESTIONS = config(
    "COURSE_FINAL_ASSESSMENT_MIN_QUESTIONS", default=15, cast=int
)
