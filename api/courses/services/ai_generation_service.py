from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.catalog.models import Category, Topic
from api.courses.enums import (
    AIGenerationItemStatus,
    AIGenerationKind,
    AIGenerationPhase,
    AIGenerationStatus,
    AssessmentLevel,
    CourseSourceType,
)
from api.courses.models import (
    AIGenerationItem,
    AIGenerationJob,
    Assessment,
    Lesson,
    Module,
)
from api.courses.services import course_service


# While a creator has a job in one of these states the provider may be working
# on their behalf, so no new AI request is accepted until it returns a result.
IN_FLIGHT_JOB_STATUSES = (
    AIGenerationStatus.QUEUED,
    AIGenerationStatus.RUNNING,
    AIGenerationStatus.STRUCTURE_READY,
)

PROGRESS_ITEMS = [
    (
        "content_objectives",
        "Analyzing course objectives",
        AIGenerationPhase.CREATING_CONTENT,
    ),
    (
        "content_outlines",
        "Generating course outlines",
        AIGenerationPhase.CREATING_CONTENT,
    ),
    (
        "content_lessons",
        "Preparing course lessons, learning objectives etc",
        AIGenerationPhase.CREATING_CONTENT,
    ),
    (
        "details_outlines",
        "Analyzing course outlines",
        AIGenerationPhase.PREPARING_DETAILS,
    ),
    (
        "details_modules",
        "Generating module information",
        AIGenerationPhase.PREPARING_DETAILS,
    ),
    (
        "details_lessons",
        "Generating course lessons, assessments, quizzes etc",
        AIGenerationPhase.PREPARING_DETAILS,
    ),
]


@transaction.atomic
def create_course_job(*, creator, validated_data):
    """Create one course job while serializing all AI requests per creator."""

    creator = type(creator).objects.select_for_update().get(pk=creator.pk)
    key = validated_data.get("idempotency_key", "")
    if key:
        existing = AIGenerationJob.objects.filter(
            creator=creator, idempotency_key=key
        ).first()
        if existing:
            return existing, False
    reject_while_in_flight(creator=creator)
    job = AIGenerationJob.objects.create(
        creator=creator,
        kind=AIGenerationKind.FULL_COURSE,
        request_payload={
            **validated_data,
            "category": str(validated_data["category"].id),
            "topic": str(validated_data["topic"].id)
            if validated_data.get("topic")
            else None,
        },
        idempotency_key=key,
    )
    AIGenerationItem.objects.bulk_create(
        [
            AIGenerationItem(
                job=job, key=item_key, label=label, phase=phase, order=index
            )
            for index, (item_key, label, phase) in enumerate(PROGRESS_ITEMS)
        ]
    )
    return job, True


@transaction.atomic
def create_short_job(*, creator, course, kind, request_payload):
    """Create an assist or thumbnail job under the same single-flight lock."""

    creator = type(creator).objects.select_for_update().get(pk=creator.pk)
    reject_while_in_flight(creator=creator)
    return AIGenerationJob.objects.create(
        creator=creator,
        course=course,
        kind=kind,
        request_payload=request_payload,
    )


def check_cancelled(job):
    job.refresh_from_db(fields=["cancel_requested"])
    if job.cancel_requested:
        job.status = AIGenerationStatus.CANCELLED
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "completed_at", "updated_datetime"])
        job.items.exclude(status=AIGenerationItemStatus.COMPLETED).update(
            status=AIGenerationItemStatus.CANCELLED
        )
        return True
    return False


def mark_item(job, key, status):
    job.items.filter(key=key).update(status=status)


@transaction.atomic
def materialize_structure(*, job, generated):
    payload = job.request_payload
    category = Category.objects.get(pk=payload["category"])
    topic = Topic.objects.get(pk=payload["topic"]) if payload.get("topic") else None
    course = course_service.create_draft_course(
        creator=job.creator,
        category=category,
        topic=topic,
        title=generated["title"],
        description=generated["description"],
        difficulty_level=generated["difficulty_level"],
        learning_objectives=generated["learning_objectives"],
        tags=generated["tags"],
        duration_seconds=generated["planned_duration_seconds"],
        terms_accepted=True,
        source_type=CourseSourceType.AI_GENERATED,
    )
    job.course = course
    job.status = AIGenerationStatus.STRUCTURE_READY
    job.stage = "Preparing course details..."
    job.save(update_fields=["course", "status", "stage", "updated_datetime"])

    for module_order, module_data in enumerate(generated["modules"], 1):
        module = Module.objects.create(
            course=course,
            title=module_data["title"],
            order=module_order,
            description=module_data["description"],
            learning_objectives=module_data["learning_objectives"],
            created_by=job.creator,
            updated_by=job.creator,
        )
        for lesson_order, lesson_data in enumerate(module_data["lessons"], 1):
            Lesson.objects.create(
                module=module,
                title=lesson_data["title"],
                order=lesson_order,
                learning_objectives=lesson_data["learning_objectives"],
                duration_minutes=lesson_data["duration_minutes"],
                created_by=job.creator,
                updated_by=job.creator,
            )
    return course


@transaction.atomic
def materialize_module_content(*, job, module, generated):
    lessons = list(module.lessons.order_by("order"))
    for lesson, lesson_data in zip(lessons, generated["lessons"], strict=True):
        lesson.script = lesson_data["script"]
        lesson.learning_objectives = lesson_data["learning_objectives"]
        lesson.duration_minutes = lesson_data["duration_minutes"]
        lesson.save(
            update_fields=[
                "script",
                "learning_objectives",
                "duration_minutes",
                "updated_datetime",
            ]
        )
    assessment = generated["assessment"]
    Assessment.objects.create(
        level=AssessmentLevel.MODULE,
        module=module,
        title=assessment["title"],
        questions=assessment["questions"],
        created_by=job.creator,
        updated_by=job.creator,
    )


@transaction.atomic
def materialize_final_assessment(*, job, generated):
    Assessment.objects.create(
        level=AssessmentLevel.COURSE,
        course=job.course,
        title=generated["title"],
        questions=generated["questions"],
        created_by=job.creator,
        updated_by=job.creator,
    )
    course_service.recalculate_duration_estimate(course=job.course)


def module_content_is_materialized(*, module) -> bool:
    return Assessment.objects.filter(
        level=AssessmentLevel.MODULE, module=module
    ).exists()


def final_assessment_is_materialized(*, course) -> bool:
    return Assessment.objects.filter(
        level=AssessmentLevel.COURSE, course=course
    ).exists()


def add_usage(*, job, usage):
    """Persist usage at each completed provider checkpoint for retry safety."""

    job.input_tokens += usage.get("input_tokens", 0)
    job.output_tokens += usage.get("output_tokens", 0)
    job.save(update_fields=["input_tokens", "output_tokens", "updated_datetime"])


def cancel_job(*, job, actor):
    if job.creator_id != actor.id:
        raise exceptions.PermissionDenied()
    if job.status in {
        AIGenerationStatus.COMPLETED,
        AIGenerationStatus.FAILED,
        AIGenerationStatus.CANCELLED,
    }:
        return job
    job.cancel_requested = True
    job.save(update_fields=["cancel_requested", "updated_datetime"])
    return job


def has_in_flight_request(*, creator, exclude_pk=None) -> bool:
    """True while any of the creator's AI jobs is queued or running.

    Provider calls are metered and the provider answers sustained concurrency
    with hard rate limits, so a creator gets exactly one AI request in flight;
    the API refuses new ones until the current job reaches a terminal state.
    """

    queryset = AIGenerationJob.objects.filter(
        creator=creator, status__in=IN_FLIGHT_JOB_STATUSES
    )
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.exists()


def reject_while_in_flight(*, creator, exclude_pk=None) -> None:
    """Refuse a new AI request (429) while one is already in flight."""

    if has_in_flight_request(creator=creator, exclude_pk=exclude_pk):
        raise exceptions.Throttled(
            detail=(
                "You already have an AI request in progress. Poll that job and "
                "wait for it to finish before starting another."
            )
        )


def resolve_assist_target(course, target_type, target_id):
    if target_type == "course":
        obj = course if target_id == course.pk else None
    elif target_type == "module":
        obj = Module.objects.filter(pk=target_id, course=course).first()
    elif target_type == "lesson":
        obj = Lesson.objects.filter(pk=target_id, module__course=course).first()
    else:
        raise exceptions.ValidationError("Unsupported AI assist target_type.")
    if not obj:
        raise exceptions.NotFound("AI assist target not found.")
    return obj
