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
    Course,
    Lesson,
    Module,
)
from api.courses.services import course_service


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


def create_course_job(*, creator, validated_data):
    key = validated_data.get("idempotency_key", "")
    if key:
        existing = AIGenerationJob.objects.filter(
            creator=creator, idempotency_key=key
        ).first()
        if existing:
            return existing, False
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


def resolve_assist_target(course, target_type, target_id):
    models = {"course": Course, "module": Module, "lesson": Lesson}
    model = models.get(target_type)
    if not model:
        raise exceptions.ValidationError("Unsupported AI assist target_type.")
    obj = model.objects.get(pk=target_id)
    related_course = (
        obj
        if target_type == "course"
        else (obj.course if target_type == "module" else obj.module.course)
    )
    if related_course.id != course.id:
        raise exceptions.PermissionDenied()
    return obj
