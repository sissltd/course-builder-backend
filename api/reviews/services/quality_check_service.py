from api.courses.models import Assessment, Course
from api.platform.services import platform_settings_service


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

    This is the automated quality gate of the review pipeline: course_service
    runs it at submission time, and reviewers see the same failures the
    submitter did. Read-only: does not mutate the course. Returns a list of
    human-readable failure messages; an empty list means the course passes.
    Thresholds are sourced from PlatformSettings (api.platform) - an Admin/
    Super Admin-editable DB row - rather than Django settings, so they can be
    tuned without a deploy.

    Deliberately out of scope: readability scoring, plagiarism scanning, bias/
    inclusivity checks, per-objective assessment-alignment checking, and media
    property validation (resolution/format/subtitles) - these require external
    NLP/plagiarism/media tooling that is not part of this Phase 1 slice.
    """

    platform_settings = platform_settings_service.get_settings()
    failures: list[str] = []
    modules = list(course.modules.all().prefetch_related("lessons", "assessment"))

    module_count = len(modules)
    if not (
        platform_settings.course_module_count_min
        <= module_count
        <= platform_settings.course_module_count_max
    ):
        failures.append(
            f"Course must have between {platform_settings.course_module_count_min} and "
            f"{platform_settings.course_module_count_max} modules (has {module_count})."
        )

    for module in modules:
        lessons = list(module.lessons.all())
        lesson_count = len(lessons)
        if not (
            platform_settings.course_lessons_per_module_min
            <= lesson_count
            <= platform_settings.course_lessons_per_module_max
        ):
            failures.append(
                f"Module '{module.title}' must have between "
                f"{platform_settings.course_lessons_per_module_min} and "
                f"{platform_settings.course_lessons_per_module_max} lessons (has {lesson_count})."
            )

        if not hasattr(module, "assessment"):
            failures.append(
                f"Module '{module.title}' is missing its module-level assessment."
            )

        for lesson in lessons:
            objective_count = len(lesson.learning_objectives or [])
            if not (
                platform_settings.course_learning_objectives_min
                <= objective_count
                <= platform_settings.course_learning_objectives_max
            ):
                failures.append(
                    f"Lesson '{lesson.title}' must have between "
                    f"{platform_settings.course_learning_objectives_min} and "
                    f"{platform_settings.course_learning_objectives_max} learning objectives "
                    f"(has {objective_count})."
                )

            script_words = _word_count(lesson.script)
            if not (
                platform_settings.lesson_script_word_min
                <= script_words
                <= platform_settings.lesson_script_word_max
            ):
                failures.append(
                    f"Lesson '{lesson.title}' script must be between "
                    f"{platform_settings.lesson_script_word_min} and {platform_settings.lesson_script_word_max} "
                    f"words (has {script_words})."
                )

            lesson_assessment = getattr(lesson, "assessment", None)
            question_count = (
                len(lesson_assessment.questions) if lesson_assessment else 0
            )
            if not (
                platform_settings.lesson_quiz_questions_min
                <= question_count
                <= platform_settings.lesson_quiz_questions_max
            ):
                failures.append(
                    f"Lesson '{lesson.title}' must have between "
                    f"{platform_settings.lesson_quiz_questions_min} and {platform_settings.lesson_quiz_questions_max} "
                    f"quiz questions (has {question_count})."
                )

    description_words = _word_count(course.description)
    if not (
        platform_settings.course_description_word_min
        <= description_words
        <= platform_settings.course_description_word_max
    ):
        failures.append(
            f"Course description must be between {platform_settings.course_description_word_min} and "
            f"{platform_settings.course_description_word_max} words (has {description_words})."
        )

    duration_minutes = get_course_duration_minutes(course)
    if not (
        platform_settings.course_duration_min_minutes
        <= duration_minutes
        <= platform_settings.course_duration_max_minutes
    ):
        failures.append(
            f"Course duration must be between {platform_settings.course_duration_min_minutes} and "
            f"{platform_settings.course_duration_max_minutes} minutes (has {duration_minutes})."
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
    if final_question_count < platform_settings.course_final_assessment_min_questions:
        failures.append(
            "Course must have a final assessment with at least "
            f"{platform_settings.course_final_assessment_min_questions} questions "
            f"(has {final_question_count})."
        )

    return failures
