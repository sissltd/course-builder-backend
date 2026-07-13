from django.conf import settings

from api.courses.models import Assessment, Course


def _word_count(text: str) -> int:
    """Count whitespace-separated words in a text field."""

    return len(text.split())


def get_course_duration_minutes(course: Course) -> int:
    """Sum Lesson.duration_minutes across all of the course's modules.

    Computed on read rather than denormalized on Course, so it can never go
    stale relative to the underlying lesson data.
    """

    return sum(
        lesson.duration_minutes
        for module in course.modules.all()
        for lesson in module.lessons.all()
    )


def validate_structural_standards(course: Course) -> list[str]:
    """Validate a course against SCCS PRD Section 6.1-6.3 structural quality standards.

    Read-only: does not mutate the course. Returns a list of human-readable
    failure messages; an empty list means the course passes. Thresholds are
    sourced from Django settings (config/settings/courses.py) so they can be
    tuned without a code change.

    Deliberately out of scope: readability scoring, plagiarism scanning, bias/
    inclusivity checks, per-objective assessment-alignment checking, and media
    property validation (resolution/format/subtitles) - these require external
    NLP/plagiarism/media tooling that is not part of this Phase 1 slice.
    """

    failures: list[str] = []
    modules = list(course.modules.all().prefetch_related("lessons", "assessment"))

    module_count = len(modules)
    if not (settings.COURSE_MODULE_COUNT_MIN <= module_count <= settings.COURSE_MODULE_COUNT_MAX):
        failures.append(
            f"Course must have between {settings.COURSE_MODULE_COUNT_MIN} and "
            f"{settings.COURSE_MODULE_COUNT_MAX} modules (has {module_count})."
        )

    for module in modules:
        lessons = list(module.lessons.all())
        lesson_count = len(lessons)
        if not (
            settings.COURSE_LESSONS_PER_MODULE_MIN
            <= lesson_count
            <= settings.COURSE_LESSONS_PER_MODULE_MAX
        ):
            failures.append(
                f"Module '{module.title}' must have between "
                f"{settings.COURSE_LESSONS_PER_MODULE_MIN} and "
                f"{settings.COURSE_LESSONS_PER_MODULE_MAX} lessons (has {lesson_count})."
            )

        if not hasattr(module, "assessment"):
            failures.append(f"Module '{module.title}' is missing its module-level assessment.")

        for lesson in lessons:
            objective_count = len(lesson.learning_objectives or [])
            if not (
                settings.COURSE_LEARNING_OBJECTIVES_MIN
                <= objective_count
                <= settings.COURSE_LEARNING_OBJECTIVES_MAX
            ):
                failures.append(
                    f"Lesson '{lesson.title}' must have between "
                    f"{settings.COURSE_LEARNING_OBJECTIVES_MIN} and "
                    f"{settings.COURSE_LEARNING_OBJECTIVES_MAX} learning objectives "
                    f"(has {objective_count})."
                )

            script_words = _word_count(lesson.script)
            if not (
                settings.LESSON_SCRIPT_WORD_MIN <= script_words <= settings.LESSON_SCRIPT_WORD_MAX
            ):
                failures.append(
                    f"Lesson '{lesson.title}' script must be between "
                    f"{settings.LESSON_SCRIPT_WORD_MIN} and {settings.LESSON_SCRIPT_WORD_MAX} "
                    f"words (has {script_words})."
                )

            lesson_assessment = getattr(lesson, "assessment", None)
            question_count = len(lesson_assessment.questions) if lesson_assessment else 0
            if not (
                settings.LESSON_QUIZ_QUESTIONS_MIN
                <= question_count
                <= settings.LESSON_QUIZ_QUESTIONS_MAX
            ):
                failures.append(
                    f"Lesson '{lesson.title}' must have between "
                    f"{settings.LESSON_QUIZ_QUESTIONS_MIN} and {settings.LESSON_QUIZ_QUESTIONS_MAX} "
                    f"quiz questions (has {question_count})."
                )

    description_words = _word_count(course.description)
    if not (
        settings.COURSE_DESCRIPTION_WORD_MIN
        <= description_words
        <= settings.COURSE_DESCRIPTION_WORD_MAX
    ):
        failures.append(
            f"Course description must be between {settings.COURSE_DESCRIPTION_WORD_MIN} and "
            f"{settings.COURSE_DESCRIPTION_WORD_MAX} words (has {description_words})."
        )

    duration_minutes = get_course_duration_minutes(course)
    if not (
        settings.COURSE_DURATION_MIN_MINUTES <= duration_minutes <= settings.COURSE_DURATION_MAX_MINUTES
    ):
        failures.append(
            f"Course duration must be between {settings.COURSE_DURATION_MIN_MINUTES} and "
            f"{settings.COURSE_DURATION_MAX_MINUTES} minutes (has {duration_minutes})."
        )

    if not course.preview_video_url:
        failures.append("Course must have a preview video before submission (BR-015).")

    if not course.terms_accepted_at:
        failures.append(
            "Creator must accept category Terms and Conditions before submission (BR-005)."
        )

    # Queried directly rather than via course.final_assessment: the reverse
    # one-to-one accessor caches on first access, which can go stale if the
    # caller already touched it (or a test mutated the DB) since this course
    # instance was loaded - a fresh query is always correct.
    final_assessment = Assessment.objects.filter(course=course).first()
    final_question_count = len(final_assessment.questions) if final_assessment else 0
    if final_question_count < settings.COURSE_FINAL_ASSESSMENT_MIN_QUESTIONS:
        failures.append(
            "Course must have a final assessment with at least "
            f"{settings.COURSE_FINAL_ASSESSMENT_MIN_QUESTIONS} questions "
            f"(has {final_question_count})."
        )

    return failures
