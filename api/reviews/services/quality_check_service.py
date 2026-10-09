from django.db.models import Sum

from api.courses.enums import LessonContentType
from api.courses.models import Assessment, Course, Lesson, LessonContentBlock
from api.platform.services import platform_settings_service
from api.reviews.enums import MediaAssetKind
from api.reviews.models import MediaAsset

#: Media assets that are video. Thumbnails, audio and subtitles are not, so a
#: text-only course may still carry them.
VIDEO_ASSET_KINDS = (MediaAssetKind.VIDEO, MediaAssetKind.PREVIEW_VIDEO)


def word_count(text: str) -> int:
    """Count whitespace-separated words in a text field."""

    return len(text.split())


def get_course_duration_minutes(course: Course) -> int:
    """Sum Lesson.duration_minutes across all of the course's modules.

    Computed on read rather than denormalized on Course, so it can never go
    stale relative to the underlying lesson data. One aggregate query,
    whatever the size of the course.
    """

    total = Lesson.objects.filter(module__course=course).aggregate(
        total=Sum("duration_minutes")
    )["total"]
    return total or 0


def _text_stage_video_failures(course: Course, lessons: list[Lesson]) -> list[str]:
    """Why a course that has not reached the video stage carries a video.

    The staged review flow reviews the text first and adds the video only
    once it passes, so a course submitted with a video of any kind is refused
    rather than reviewed with media the reviewer was never meant to see.
    """

    failures: list[str] = []
    if course.preview_video_url:
        failures.append(
            "Remove the preview video: video is added after the text passes review."
        )
    for lesson in lessons:
        if lesson.video_url or lesson.embedded_link:
            failures.append(
                f"Lesson '{lesson.title}' must not have a video yet: video is "
                "added after the text passes review."
            )
    if LessonContentBlock.objects.filter(
        lesson__module__course=course,
        block_type=LessonContentBlock.BlockType.VIDEO,
    ).exists():
        failures.append(
            "Remove the video blocks from the lesson bodies: video is added "
            "after the text passes review."
        )
    if MediaAsset.objects.filter(course=course, kind__in=VIDEO_ASSET_KINDS).exists():
        failures.append(
            "Remove the registered video assets: video is added after the "
            "text passes review."
        )
    return failures


def _video_stage_failures(course: Course, lessons: list[Lesson]) -> list[str]:
    """What a course that has reached the video stage still needs.

    The preview video (BR-015) and a media reference on every video lesson are
    required from here on. Media property checks stay with QA.
    """

    failures: list[str] = []
    if not course.preview_video_url:
        failures.append("Course must have a preview video before submission (BR-015).")
    for lesson in lessons:
        if lesson.content_type == LessonContentType.VIDEO and not (
            lesson.video_url or lesson.embedded_link
        ):
            failures.append(
                f"Video lesson '{lesson.title}' requires a video_url or embedded_link."
            )
    return failures


def validate_structural_standards(course: Course) -> list[str]:
    """Validate a course against SCCS PRD Section 6.1-6.3 structural quality standards.

    This is the automated quality gate of the review pipeline: course_service
    runs it at submission time, and reviewers see the same failures the
    submitter did. Read-only: does not mutate the course. Returns a list of
    human-readable failure messages; an empty list means the course passes.
    Thresholds are sourced from PlatformSettings (api.platform) - an Admin/
    Super Admin-editable DB row - rather than Django settings, so they can be
    tuned without a deploy.

    With PlatformSettings.staged_review_flow_enabled on, the BR-015 preview
    video requirement moves: a course that has not reached the video stage
    (Course.video_attached_at is null) must carry no video at all, and one
    that has must carry the preview video and a media reference on every
    video lesson.

    Deliberately out of scope: readability scoring, plagiarism scanning, bias/
    inclusivity checks, per-objective assessment-alignment checking, and media
    property validation (resolution/format/subtitles) - these require external
    NLP/plagiarism/media tooling that is not part of this Phase 1 slice.
    """

    platform_settings = platform_settings_service.get_settings()
    failures: list[str] = []
    modules = list(course.modules.all().prefetch_related("lessons", "assessment"))

    course_objective_count = len(course.learning_objectives or [])
    if not (
        platform_settings.course_learning_objectives_min
        <= course_objective_count
        <= platform_settings.course_learning_objectives_max
    ):
        failures.append(
            "Course must have between "
            f"{platform_settings.course_learning_objectives_min} and "
            f"{platform_settings.course_learning_objectives_max} learning objectives "
            f"(has {course_objective_count})."
        )

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
                platform_settings.lesson_learning_objectives_min
                <= objective_count
                <= platform_settings.lesson_learning_objectives_max
            ):
                failures.append(
                    f"Lesson '{lesson.title}' must have between "
                    f"{platform_settings.lesson_learning_objectives_min} and "
                    f"{platform_settings.lesson_learning_objectives_max} learning objectives "
                    f"(has {objective_count})."
                )

            if lesson.content_type == LessonContentType.TEXT:
                script_words = word_count(lesson.script)
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

    description_words = word_count(course.description)
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

    if not platform_settings.staged_review_flow_enabled:
        if not course.preview_video_url:
            failures.append(
                "Course must have a preview video before submission (BR-015)."
            )
    else:
        # Which video rule applies depends on whether the course has reached
        # the video stage, so every caller (submission, the reviewers' check
        # run) judges it the same way.
        lessons = [lesson for module in modules for lesson in module.lessons.all()]
        failures.extend(
            _video_stage_failures(course, lessons)
            if course.video_attached_at
            else _text_stage_video_failures(course, lessons)
        )

    if not course.version_id:
        failures.append("Course version must be selected before submission.")

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
