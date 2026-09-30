"""MIE course push: the developer of an approved idea sends the finished course.

The pushed course joins the normal Course Creator flow - content review, QA
verification, publication - with no parallel pipeline. It is owned by the
developer's linked creator account (developer_service.get_or_create_creator_user)
and marked CourseSourceType.DEVELOPER_API.

A push is all-or-nothing. The gate runs first: the idea must be the
caller's, APPROVED, and the course title must be the idea's title. Then the
course is built and submitted in one transaction, so a push that fails
submission's structural check leaves nothing behind.

A push creates the idea's course the first time. After a reviewer sends that
course back to DRAFT, the next push replaces its content and resubmits it.
While the course is in review or published it cannot be replaced.

Only CREATOR_UPLOADED courses are paid out automatically
(transaction_services.effect_course_payment), so a pushed course carries its
price snapshot and nothing else: the developer payout is not built yet.
"""

from django.conf import settings as django_settings
from django.db import transaction
from rest_framework import exceptions

from api.catalog.enums import CategoryStatus
from api.catalog.models import Category
from api.courses.constants import COURSE_MEDIA_URL_MAX_LENGTH
from api.courses.enums import (
    AssessmentLevel,
    CourseSourceType,
    CourseStatus,
    DifficultyLevel,
    LessonContentType,
    QuestionType,
)
from api.courses.models import (
    Assessment,
    Course,
    CourseVersion,
    Lesson,
    LessonContentBlock,
    LessonRequirement,
    Module,
)
from api.courses.serializers.assessment_serializer import (
    MAXIMUM_CHOICE_OPTIONS,
    MINIMUM_CHOICE_OPTIONS,
)
from api.courses.services import course_service
from api.mie.enums import SubmissionStatus, WebhookEventType
from api.mie.exceptions import CourseAlreadyInFlight, IdeaNotApproved
from api.mie.models import CourseSubmission, DeveloperAccount, WebhookEvent
from api.mie.serializers.course_push_serializer import (
    MAX_PUSH_BLOCKS_PER_LESSON,
    MAX_PUSH_LESSONS_PER_MODULE,
    MAX_PUSH_MODULES,
)
from api.mie.services import developer_service
from api.platform.services import platform_settings_service
from api.reviews.enums import ReviewActionType
from api.reviews.models import ReviewAction
from shared.services.storage_service import (
    COURSE_UPLOAD_RULES,
    MIN_MEDIA_HEIGHT,
    MIN_MEDIA_WIDTH,
)

LESSON_FIELDS = (
    "title",
    "order",
    "content_type",
    "script",
    "video_url",
    "embedded_link",
    "video_script_file",
    "learning_objectives",
    "duration_minutes",
)
"""Validated lesson keys that map straight onto Lesson columns."""


# >>>>>>>>>>>>>>>>>>>> Push <<<<<<<<<<<<<<<<<<<<<<


def push_course(*, developer: DeveloperAccount, submission_id, data: dict) -> Course:
    """Build (or rebuild) and submit the course for one approved idea.

    `data` is CoursePushSerializer.validated_data. Raises NotFound for an
    idea that is not the caller's, IdeaNotApproved (409) for one that is not
    APPROVED, CourseAlreadyInFlight (409) for one whose course is past
    DRAFT, and ValidationError for a title or category that does not match
    the idea, or a course that fails the structural check at submit. On any
    of those nothing is written.
    """

    with transaction.atomic():
        submission = _lock_own_submission(
            developer=developer, submission_id=submission_id
        )
        _check_gate(submission=submission, data=data)
        creator = developer_service.get_or_create_creator_user(developer=developer)

        course = submission.resulting_course
        if course is None:
            course = _create_course(submission=submission, creator=creator, data=data)
        else:
            _replace_course(
                course=course, submission=submission, creator=creator, data=data
            )

        _build_content(course=course, creator=creator, modules=data["modules"])
        _set_final_assessment(
            course=course, creator=creator, assessment=data.get("final_assessment")
        )
        course_service.recalculate_duration_estimate(course=course)
        course_service.submit_course(course=course, actor=creator, skip_draft_hold=True)

        if submission.resulting_course_id != course.id:
            submission.resulting_course = course
            submission.save(update_fields=["resulting_course", "updated_datetime"])
        record_course_event(course=course, event_type=WebhookEventType.COURSE_SUBMITTED)
    return course


def _lock_own_submission(*, developer, submission_id) -> CourseSubmission:
    """The caller's submission, row-locked so two pushes for the same idea
    run one after the other. Another developer's idea is a 404, never a
    403, so ids cannot be probed."""

    submission = (
        CourseSubmission.objects.select_for_update()
        .filter(developer=developer, id=submission_id)
        .first()
    )
    if submission is None:
        raise exceptions.NotFound("Submission not found.")
    return submission


def _check_gate(*, submission: CourseSubmission, data: dict) -> None:
    """Refuse the push before anything is built."""

    if submission.status != SubmissionStatus.APPROVED:
        raise IdeaNotApproved(
            "A course can only be pushed for an approved idea. This idea is "
            f"{submission.status}."
        )
    course = submission.resulting_course
    if course is not None and course.status != CourseStatus.DRAFT:
        raise CourseAlreadyInFlight(
            "This idea's course is already "
            f"{course.status}. It can only be replaced after a reviewer "
            "sends it back to DRAFT."
        )
    if _normalise(data["title"]) != _normalise(submission.title):
        raise exceptions.ValidationError(
            {
                "title": [
                    "The course title must be the approved idea's title: "
                    f"'{submission.title}'."
                ]
            }
        )
    category = data["category"]
    if submission.category_id and category.id != submission.category_id:
        raise exceptions.ValidationError(
            {
                "category": [
                    "The course category must be the approved idea's "
                    f"category: '{submission.category.name}'."
                ]
            }
        )
    if category.status != CategoryStatus.ACTIVE:
        raise exceptions.ValidationError(
            {"category": ["This category is not currently accepting new courses."]}
        )
    if not data["terms_accepted"]:
        raise exceptions.ValidationError(
            {
                "terms_accepted": [
                    "You must accept the category Terms and Conditions to "
                    "submit a course."
                ]
            }
        )


def _normalise(title: str) -> str:
    """The comparison the dedup engine uses: trimmed, case-insensitive."""

    return title.strip().casefold()


def _planned_duration_seconds(data: dict) -> int:
    return (
        data.get("duration_hours", 0) * 3600
        + data.get("duration_minutes", 0) * 60
        + data.get("duration_seconds", 0)
    )


def _create_course(*, submission, creator, data: dict) -> Course:
    """First push: a new DRAFT course through the creator's own entry point.

    The title stored is the idea's title verbatim - the payload's only has
    to match it - so the course and the idea always read the same.
    """

    return course_service.create_draft_course(
        creator=creator,
        category=data["category"],
        title=submission.title,
        description=data["description"],
        preview_video_url=data.get("preview_video_url", ""),
        thumbnail_url=data.get("thumbnail_url", ""),
        difficulty_level=data.get("difficulty_level", ""),
        learning_objectives=data.get("learning_objectives"),
        tags=data.get("tags"),
        version=data.get("version"),
        duration_hours=data.get("duration_hours", 0),
        duration_minutes=data.get("duration_minutes", 0),
        duration_seconds=data.get("duration_seconds", 0),
        terms_accepted=data["terms_accepted"],
        source_type=CourseSourceType.DEVELOPER_API,
    )


def _replace_course(*, course: Course, submission, creator, data: dict) -> None:
    """Re-push after review sent the course back: overwrite every course
    field and drop the old content, so the course is exactly this push.

    Review history survives: ReviewAction rows hang off the course, and the
    ReviewFlag links to deleted lessons and modules are SET_NULL.
    """

    course_service.update_draft_course(
        course=course,
        actor=creator,
        data={
            "title": submission.title,
            "description": data["description"],
            "preview_video_url": data.get("preview_video_url", ""),
            "thumbnail_url": data.get("thumbnail_url", ""),
            "category": data["category"],
            "difficulty_level": data.get("difficulty_level", ""),
            "learning_objectives": data.get("learning_objectives") or [],
            "tags": data.get("tags") or [],
            "version": data.get("version"),
            "planned_duration_seconds": _planned_duration_seconds(data),
        },
    )
    Module.objects.filter(course=course).delete()
    Assessment.objects.filter(course=course).delete()


def _build_content(*, course: Course, creator, modules: list[dict]) -> None:
    """Insert the whole module tree with one bulk insert per table, so the
    query count does not grow with the size of the course.

    Ids are UUIDs assigned in Python, so children can point at parents
    before anything is written.
    """

    stamp = {"created_by": creator, "updated_by": creator}
    module_rows, lesson_rows, block_rows, requirement_rows, assessment_rows = (
        [],
        [],
        [],
        [],
        [],
    )

    for module_data in modules:
        module = Module(
            course=course,
            title=module_data["title"],
            order=module_data["order"],
            description=module_data.get("description", ""),
            learning_objectives=module_data.get("learning_objectives", []),
            **stamp,
        )
        module_rows.append(module)
        if module_data.get("assessment"):
            assessment_rows.append(
                _assessment(
                    module_data["assessment"],
                    level=AssessmentLevel.MODULE,
                    module=module,
                    **stamp,
                )
            )

        for lesson_data in module_data["lessons"]:
            lesson = Lesson(
                module=module,
                **{
                    field: lesson_data[field]
                    for field in LESSON_FIELDS
                    if field in lesson_data
                },
                **stamp,
            )
            lesson_rows.append(lesson)
            block_rows += [
                LessonContentBlock(lesson=lesson, **block, **stamp)
                for block in lesson_data.get("content_blocks", [])
            ]
            requirement_rows += [
                LessonRequirement(
                    lesson=lesson,
                    text=requirement["text"],
                    order=requirement["order"],
                    **stamp,
                )
                for requirement in lesson_data.get("requirements", [])
            ]
            if lesson_data.get("assessment"):
                assessment_rows.append(
                    _assessment(
                        lesson_data["assessment"],
                        level=AssessmentLevel.LESSON,
                        lesson=lesson,
                        **stamp,
                    )
                )

    Module.objects.bulk_create(module_rows)
    Lesson.objects.bulk_create(lesson_rows)
    LessonContentBlock.objects.bulk_create(block_rows)
    LessonRequirement.objects.bulk_create(requirement_rows)
    Assessment.objects.bulk_create(assessment_rows)


def _set_final_assessment(*, course: Course, creator, assessment: dict | None) -> None:
    if assessment:
        _assessment(
            assessment,
            level=AssessmentLevel.COURSE,
            course=course,
            created_by=creator,
            updated_by=creator,
        ).save()


def _assessment(data: dict, *, level: str, **parent_and_stamp) -> Assessment:
    return Assessment(
        level=level,
        title=data["title"],
        questions=[dict(question) for question in data["questions"]],
        **parent_and_stamp,
    )


# >>>>>>>>>>>>>>>>>>>> Status <<<<<<<<<<<<<<<<<<<<<<


def course_status(*, developer: DeveloperAccount, submission_id) -> dict:
    """Where the course pushed for one of the caller's ideas stands.

    404 for another developer's idea, and for one of the caller's ideas
    that has no course yet.
    """

    submission = (
        CourseSubmission.objects.select_related("resulting_course")
        .filter(developer=developer, id=submission_id)
        .first()
    )
    if submission is None:
        raise exceptions.NotFound("Submission not found.")
    if submission.resulting_course is None:
        raise exceptions.NotFound("No course has been pushed for this idea yet.")
    return status_payload(course=submission.resulting_course, submission=submission)


def status_payload(*, course: Course, submission: CourseSubmission) -> dict:
    """The DevCourseSerializer shape for one course."""

    lesson_count = Lesson.objects.filter(module__course=course).count()
    return {
        "submission_id": submission.id,
        "submission_reference": submission.public_reference,
        "course_id": course.id,
        "title": course.title,
        "status": course.status,
        "module_count": Module.objects.filter(course=course).count(),
        "lesson_count": lesson_count,
        "submitted_at": course.submitted_at,
        "rejected_at": course.rejected_at,
        "published_at": course.published_at,
        "revision_feedback": (
            _latest_revision_feedback(course)
            if course.status == CourseStatus.DRAFT and course.rejected_at
            else None
        ),
    }


def _latest_revision_feedback(course: Course) -> dict | None:
    review_action = (
        ReviewAction.objects.filter(course=course, action=ReviewActionType.REJECT)
        .order_by("-created_datetime")
        .first()
    )
    return revision_feedback(review_action) if review_action else None


def revision_feedback(review_action: ReviewAction) -> dict:
    """What a developer needs to fix: the reviewer's feedback and each
    flagged issue, named by module and lesson title."""

    return {
        "stage": review_action.stage,
        "rejected_at": review_action.created_datetime.isoformat(),
        "feedback": review_action.feedback,
        "flags": [
            {
                "flag_type": flag.flag_type,
                "title": flag.title,
                "system_message": flag.system_message,
                "reviewer_note": flag.reviewer_note,
                "module_title": flag.module.title if flag.module else None,
                "lesson_title": flag.lesson.title if flag.lesson else None,
            }
            for flag in review_action.flags.select_related("lesson", "module")
        ],
    }


# >>>>>>>>>>>>>>>>>>>> Requirements <<<<<<<<<<<<<<<<<<<<<<


MIE_UPLOAD_PURPOSES = (
    "COURSE_PREVIEW_VIDEO",
    "LESSON_VIDEO",
    "LESSON_IMAGE",
    "COURSE_THUMBNAIL",
    "SUBTITLE",
)
"""Course-media upload purposes a developer may presign. The creator-only
document import is left out: a pushed course is already structured."""


def course_requirements() -> dict:
    """The live values a pushed course is checked against, and the ids it
    must reference. Read from the same PlatformSettings row, catalogue and
    upload rules the creator builder uses, so it is always current."""

    settings_row = platform_settings_service.get_settings()
    return {
        "structural_rules": {
            "course_learning_objectives": _range(
                settings_row, "course_learning_objectives"
            ),
            "modules_per_course": _range(settings_row, "course_module_count"),
            "lessons_per_module": _range(settings_row, "course_lessons_per_module"),
            "lesson_learning_objectives": _range(
                settings_row, "lesson_learning_objectives"
            ),
            "text_lesson_script_words": _range(settings_row, "lesson_script_word"),
            "course_description_words": _range(settings_row, "course_description_word"),
            "course_duration_minutes": {
                "min": settings_row.course_duration_min_minutes,
                "max": settings_row.course_duration_max_minutes,
            },
            "final_assessment_min_questions": (
                settings_row.course_final_assessment_min_questions
            ),
            "module_assessment_required": True,
            "preview_video_required": True,
            "course_version_required": True,
        },
        "categories": [
            {"id": str(category.id), "name": category.name, "slug": category.slug}
            for category in Category.objects.filter(
                status=CategoryStatus.ACTIVE
            ).order_by("name")
        ],
        "course_versions": [
            {"id": str(version.id), "label": version.label}
            for version in CourseVersion.objects.filter(is_active=True).order_by(
                "label"
            )
        ],
        "choices": {
            "difficulty_level": DifficultyLevel.values,
            "lesson_type": LessonContentType.values,
            "content_block_type": [
                block_type
                for block_type in LessonContentBlock.BlockType.values
                if block_type != LessonContentBlock.BlockType.QUIZ
            ],
            "question_type": QuestionType.values,
        },
        "quiz_rules": {
            "choice_options": {
                "min": MINIMUM_CHOICE_OPTIONS,
                "max": MAXIMUM_CHOICE_OPTIONS,
            },
        },
        "payload_limits": {
            "modules": MAX_PUSH_MODULES,
            "lessons_per_module": MAX_PUSH_LESSONS_PER_MODULE,
            "content_blocks_per_lesson": MAX_PUSH_BLOCKS_PER_LESSON,
            "media_url_max_length": COURSE_MEDIA_URL_MAX_LENGTH,
            "request_body_max_bytes": django_settings.DATA_UPLOAD_MAX_MEMORY_SIZE,
        },
        "upload_purposes": [
            _upload_rule(purpose, COURSE_UPLOAD_RULES[purpose])
            for purpose in MIE_UPLOAD_PURPOSES
        ],
    }


def _range(settings_row, prefix: str) -> dict:
    return {
        "min": getattr(settings_row, f"{prefix}_min"),
        "max": getattr(settings_row, f"{prefix}_max"),
    }


def _upload_rule(purpose: str, rule: dict) -> dict:
    return {
        "purpose": purpose,
        "content_types": sorted(rule["content_types"]),
        "extensions": sorted(rule["extensions"]),
        "max_size_bytes": rule["max_size"],
        "min_resolution": (
            {"width": MIN_MEDIA_WIDTH, "height": MIN_MEDIA_HEIGHT}
            if rule.get("dimensions")
            else None
        ),
        "aspect_ratio": (
            "{}:{}".format(*rule["aspect_ratio"]) if "aspect_ratio" in rule else None
        ),
        "codec": rule.get("codec"),
        "duration_seconds": (
            {"min": rule["duration_range"][0], "max": rule["duration_range"][1]}
            if "duration_range" in rule
            else None
        ),
    }


# >>>>>>>>>>>>>>>>>>>> Webhooks <<<<<<<<<<<<<<<<<<<<<<


def record_course_event(
    *, course: Course, event_type: str, review_action: ReviewAction | None = None
) -> WebhookEvent | None:
    """Record a COURSE_* webhook for a pushed course; a no-op for any other.

    The course and review services call this at every move a developer
    must hear about, so it has to cost a creator's course nothing: it
    returns before touching the database unless the course came in
    through the push.
    """

    if course.source_type != CourseSourceType.DEVELOPER_API:
        return None
    submission = (
        CourseSubmission.objects.select_related("developer")
        .filter(resulting_course=course)
        .first()
    )
    if submission is None:
        return None

    body = {
        "reference": submission.public_reference,
        "status": submission.status,
        "title": submission.title,
        "course": {
            "id": str(course.id),
            "status": course.status,
            "title": course.title,
        },
    }
    if review_action is not None:
        body["course"]["revision_feedback"] = revision_feedback(review_action)
    return WebhookEvent.objects.create(
        submission=submission,
        event_type=event_type,
        payload={"submission": body, "developer_email": submission.developer.email},
    )
