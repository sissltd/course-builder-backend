import math
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import exceptions

from api.catalog.models import Category, Topic
from api.courses.ai.providers import (
    LESSON_DURATION_MAX_MINUTES,
    LESSON_DURATION_MIN_MINUTES,
    GenerationStandards,
)
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
    CourseVersion,
    Lesson,
    Module,
)
from api.courses.services import course_service
from api.platform.services import platform_settings_service
from api.reviews.services import quality_check_service


# Backend-published AI generation progress events are sent on one Redis
# channel per user so SSE subscribers can render progress without polling.
AI_GENERATION_CHANNEL_TEMPLATE = "user:ai-generations:{user_id}"


def _ai_generation_channel(user_id):
    return AI_GENERATION_CHANNEL_TEMPLATE.format(user_id=user_id)


def publish_ai_generation_progress(*, job, payload):
    """Schedule an async publish of a generation progress event for the job owner.

    This is best-effort and tolerant of Redis outages: a missing or broken
    channel should not prevent generation from completing or surface any
    user-facing error.
    """
    try:
        from asgiref.sync import async_to_sync
        from shared.redis.redis_service import RedisService

        async_to_sync(RedisService.publish_ai_generation_progress)(
            job_id=job.id,
            user_id=job.creator_id,
            payload=payload,
        )
    except Exception:
        return


# While a creator has a job in one of these states the provider may be working
# on their behalf, so no new AI request is accepted until it returns a result.
IN_FLIGHT_JOB_STATUSES = (
    AIGenerationStatus.QUEUED,
    AIGenerationStatus.RUNNING,
    AIGenerationStatus.STRUCTURE_READY,
)
# A worker holds the job in one of these states while executing it; both must
# keep heartbeating so duplicate deliveries and the watchdog see a live claim.
EXECUTING_JOB_STATUSES = (
    AIGenerationStatus.RUNNING,
    AIGenerationStatus.STRUCTURE_READY,
)
STALE_IN_FLIGHT_AFTER = timedelta(hours=6)
QUEUE_REDISPATCH_AFTER = timedelta(minutes=5)
HEARTBEAT_TIMEOUT = timedelta(minutes=10)
MAX_DISPATCH_ATTEMPTS = 3
MAX_RETRY_COUNT = 3
STALE_JOB_ERROR_MESSAGE = (
    "AI generation did not complete within the expected time. Please start a new "
    "generation request."
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


def _ends_sentence(value: str) -> bool:
    return value.rstrip().endswith((".", "!", "?"))


def _looks_like_comma_fragment(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    first = stripped[0]
    return first.islower() or stripped.lower().startswith(
        ("and ", "or ", "but ", "including ", "such as ", "as well as ")
    )


def normalize_ai_learning_objectives(objectives: list) -> list[str]:
    """Repair AI arrays that split one objective at comma-separated clauses.

    The provider is asked for an array of complete objectives, but it can
    occasionally return fragments such as ["Work with parameters", "arguments",
    "return values", "and default parameters."]. Only apply this to
    sentence-like AI output: manual creator lists such as ["a", "b"] or terse
    provider lists without punctuation are left as-is.
    """

    cleaned = [str(item).strip() for item in objectives if str(item).strip()]
    if not any(_ends_sentence(item) for item in cleaned):
        return cleaned

    normalized: list[str] = []
    for item in cleaned:
        if normalized and _looks_like_comma_fragment(item):
            separator = (
                " " if item.lower().startswith(("and ", "or ", "but ")) else ", "
            )
            normalized[-1] = f"{normalized[-1].rstrip(' ,')}{separator}{item}"
        else:
            normalized.append(item)
    return normalized


def get_generation_standards() -> GenerationStandards:
    """The submission thresholds generation must meet, from PlatformSettings."""

    return GenerationStandards.from_platform_settings(
        platform_settings_service.get_settings()
    )


def _rescaled_durations(
    durations: list[int], *, min_total: int, max_total: int
) -> list[int]:
    """Scale lesson durations proportionally so their sum lands in range.

    Rounds up when growing and down when shrinking, so the per-lesson rounding
    never pushes the total back across the bound it was scaled to. Returns the
    input unchanged when it is already in range (or empty).
    """

    total = sum(durations)
    if not durations or min_total <= total <= max_total:
        return durations
    target = min_total if total < min_total else max_total
    factor = target / total if total else 0
    rounding = math.ceil if total < min_total else math.floor
    return [
        max(
            LESSON_DURATION_MIN_MINUTES,
            min(LESSON_DURATION_MAX_MINUTES, rounding(duration * factor)),
        )
        for duration in durations
    ]


def repair_outline(*, outline: dict, standards: GenerationStandards, provider):
    """Fix the outline fields a JSON schema cannot constrain.

    Word counts and summed durations are not expressible in the provider's
    structured-output schema, so they are checked here with the same rules as
    the submission quality check. A short/long description gets one rewrite
    through the existing assist call; durations are rescaled locally. Returns
    (outline, usage) where usage covers any extra provider call.
    """

    usage = {"input_tokens": 0, "output_tokens": 0}
    description = outline["description"]
    words = quality_check_service.word_count(description)
    if not (
        standards.description_words_min <= words <= standards.description_words_max
    ):
        rewritten, assist_usage = provider.generate_assist(
            target="course description",
            current_value=description,
            instruction=(
                f"Rewrite this as a learner-facing course description of "
                f"{standards.description_words_min}-"
                f"{standards.description_words_max} words (aim for about "
                f"{standards.description_words_target}). Cover who the course "
                "is for, what learners will be able to do, and how the course "
                "is structured. Return plain prose only."
            ),
            context={
                "title": outline["title"],
                "learning_objectives": outline["learning_objectives"],
                "modules": [module["title"] for module in outline["modules"]],
            },
        )
        usage["input_tokens"] += assist_usage.get("input_tokens", 0)
        usage["output_tokens"] += assist_usage.get("output_tokens", 0)
        rewritten = (rewritten or "").strip()
        if (
            standards.description_words_min
            <= quality_check_service.word_count(rewritten)
            <= standards.description_words_max
        ):
            outline = {**outline, "description": rewritten}

    lessons = [
        lesson for module in outline["modules"] for lesson in module["lessons"]
    ]
    durations = _rescaled_durations(
        [lesson["duration_minutes"] for lesson in lessons],
        min_total=standards.duration_min_minutes,
        max_total=standards.duration_max_minutes,
    )
    for lesson, duration in zip(lessons, durations, strict=True):
        lesson["duration_minutes"] = duration
    return outline, usage


def rescale_course_lesson_durations(*, course, standards: GenerationStandards):
    """Bring stored lesson durations back in range after module content runs.

    The module-content call may adjust per-lesson durations, so the outline
    repair alone cannot guarantee the final course total.
    """

    lessons = list(Lesson.objects.filter(module__course=course).order_by("pk"))
    durations = _rescaled_durations(
        [lesson.duration_minutes for lesson in lessons],
        min_total=standards.duration_min_minutes,
        max_total=standards.duration_max_minutes,
    )
    changed = []
    for lesson, duration in zip(lessons, durations, strict=True):
        if lesson.duration_minutes != duration:
            lesson.duration_minutes = duration
            changed.append(lesson)
    if changed:
        Lesson.objects.bulk_update(changed, ["duration_minutes"])


def _default_course_version():
    return (
        CourseVersion.objects.filter(is_active=True)
        .order_by("-created_datetime")
        .first()
    )


def quality_report(*, course) -> list[str]:
    """Submission checks the generated course still fails (e.g. preview video)."""

    return quality_check_service.validate_structural_standards(course)


@transaction.atomic
def create_course_job(*, creator, validated_data):
    """Create one course job while serializing all AI requests per creator."""

    creator = type(creator).objects.select_for_update().get(pk=creator.pk)
    fail_stale_in_flight_jobs(creator=creator)
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
    fail_stale_in_flight_jobs(creator=creator)
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
        job.stage = "Generation cancelled"
        job.completed_at = timezone.now()
        job.save(
            update_fields=["status", "stage", "completed_at", "updated_datetime"]
        )
        job.items.exclude(status=AIGenerationItemStatus.COMPLETED).update(
            status=AIGenerationItemStatus.CANCELLED
        )
        publish_ai_generation_progress(
            job=job,
            payload={"type": "cancelled", "stage": job.stage},
        )
        return True
    return False


@transaction.atomic
def claim_job_for_execution(*, job_id, task_id, stage):
    """Atomically claim a queued or abandoned task execution.

    Celery can deliver a task twice during broker recovery. Only the first
    worker claim is allowed to execute; a recent running claim makes later
    deliveries harmless no-ops.
    """

    job = AIGenerationJob.objects.select_for_update().get(pk=job_id)
    if job.status in {
        AIGenerationStatus.COMPLETED,
        AIGenerationStatus.FAILED,
        AIGenerationStatus.CANCELLED,
    }:
        return False
    now = timezone.now()
    heartbeat = job.last_heartbeat_at or job.updated_datetime
    if (
        job.status in EXECUTING_JOB_STATUSES
        and heartbeat
        and now - heartbeat < HEARTBEAT_TIMEOUT
    ):
        return False
    job.status = AIGenerationStatus.RUNNING
    job.stage = stage
    job.celery_task_id = task_id or job.celery_task_id
    job.started_at = job.started_at or now
    job.last_heartbeat_at = now
    job.save(
        update_fields=[
            "status",
            "stage",
            "celery_task_id",
            "started_at",
            "last_heartbeat_at",
            "updated_datetime",
        ]
    )
    return True


def heartbeat(*, job):
    """Record that a worker is still executing the job."""

    now = timezone.now()
    AIGenerationJob.objects.filter(
        pk=job.pk, status__in=EXECUTING_JOB_STATUSES
    ).update(last_heartbeat_at=now, updated_datetime=now)


@transaction.atomic
def queue_job_for_retry(*, job, stage):
    """Queue a retry only if cancellation or another terminal state has not won."""

    job = AIGenerationJob.objects.select_for_update().get(pk=job.pk)
    if job.status in {
        AIGenerationStatus.COMPLETED,
        AIGenerationStatus.FAILED,
        AIGenerationStatus.CANCELLED,
    }:
        return False
    if job.cancel_requested:
        check_cancelled(job)
        return False
    job.status = AIGenerationStatus.QUEUED
    job.stage = stage
    job.last_heartbeat_at = None
    job.save(
        update_fields=["status", "stage", "last_heartbeat_at", "updated_datetime"]
    )
    return True


@transaction.atomic
def prepare_job_retry(*, job, actor):
    """Reset a failed/cancelled job for a bounded, resumable retry."""

    if job.creator_id != actor.id:
        raise exceptions.PermissionDenied()
    job = AIGenerationJob.objects.select_for_update().get(pk=job.pk)
    if job.status not in {
        AIGenerationStatus.FAILED,
        AIGenerationStatus.CANCELLED,
    }:
        raise exceptions.ValidationError(
            "Only failed or cancelled AI generations can be retried."
        )
    if job.retry_count >= MAX_RETRY_COUNT:
        raise exceptions.ValidationError(
            "This AI generation has reached its maximum retry count."
        )
    job.status = AIGenerationStatus.QUEUED
    job.stage = "Queued for retry"
    job.error_message = ""
    job.cancel_requested = False
    job.completed_at = None
    job.last_heartbeat_at = None
    job.last_dispatch_at = None
    job.dispatch_attempts = 0
    job.retry_count += 1
    job.save(
        update_fields=[
            "status",
            "stage",
            "error_message",
            "cancel_requested",
            "completed_at",
            "last_heartbeat_at",
            "last_dispatch_at",
            "dispatch_attempts",
            "retry_count",
            "updated_datetime",
        ]
    )
    job.items.exclude(status=AIGenerationItemStatus.COMPLETED).update(
        status=AIGenerationItemStatus.PENDING, error_message=""
    )
    return job


def mark_item(job, key, status):
    job.items.filter(key=key).update(status=status)


def fail_job(*, job, message):
    """Record a terminal failure on a generation job."""

    job.status = AIGenerationStatus.FAILED
    job.stage = "Generation failed"
    job.error_message = str(message)[:4000]
    job.completed_at = timezone.now()
    job.save(
        update_fields=[
            "status",
            "stage",
            "error_message",
            "completed_at",
            "updated_datetime",
        ]
    )
    job.items.filter(
        status__in=[AIGenerationItemStatus.PENDING, AIGenerationItemStatus.RUNNING]
    ).update(status=AIGenerationItemStatus.FAILED, error_message=str(message)[:4000])


def fail_stale_in_flight_jobs(*, creator):
    """Release creator-owned AI jobs that can no longer be completed."""

    stale_before = timezone.now() - STALE_IN_FLIGHT_AFTER
    stale_jobs = AIGenerationJob.objects.filter(creator=creator).filter(
        Q(
            status=AIGenerationStatus.QUEUED,
            updated_datetime__lt=stale_before,
        )
        | Q(
            status__in=EXECUTING_JOB_STATUSES,
            last_heartbeat_at__lt=stale_before,
        )
        | Q(
            status__in=EXECUTING_JOB_STATUSES,
            last_heartbeat_at__isnull=True,
            updated_datetime__lt=stale_before,
        )
    )
    for job in stale_jobs.prefetch_related("items"):
        if job.cancel_requested:
            job.status = AIGenerationStatus.CANCELLED
            job.stage = "Generation cancelled"
            job.completed_at = timezone.now()
            job.save(
                update_fields=[
                    "status",
                    "stage",
                    "completed_at",
                    "updated_datetime",
                ]
            )
            job.items.filter(
                status__in=[
                    AIGenerationItemStatus.PENDING,
                    AIGenerationItemStatus.RUNNING,
                ]
            ).update(status=AIGenerationItemStatus.CANCELLED)
        else:
            fail_job(job=job, message=STALE_JOB_ERROR_MESSAGE)


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
        learning_objectives=normalize_ai_learning_objectives(
            generated["learning_objectives"]
        ),
        tags=generated["tags"],
        duration_seconds=generated["planned_duration_seconds"],
        version=_default_course_version(),
        terms_accepted=True,
        source_type=CourseSourceType.AI_GENERATED,
    )
    job.course = course
    job.status = AIGenerationStatus.STRUCTURE_READY
    job.stage = "Preparing course details..."
    job.save(update_fields=["course", "status", "stage", "updated_datetime"])
    publish_ai_generation_progress(
        job=job,
        payload={
            "type": "phase",
            "phase": "PREPARING_DETAILS",
            "stage": job.stage,
            "items": [
                {
                    "key": item.key,
                    "label": item.label,
                    "status": item.status,
                }
                for item in job.items.order_by("order")
            ],
        },
    )

    total_modules = len(generated["modules"])
    for module_order, module_data in enumerate(generated["modules"], 1):
        module_index = module_order - 1
        publish_ai_generation_progress(
            job=job,
            payload={
                "type": "module",
                "module_index": module_index,
                "module_count": total_modules,
                "module": {
                    "title": module_data["title"],
                    "order": module_order,
                },
                "stage": f"Generating module {module_order} of {total_modules}...",
            },
        )
        module = Module.objects.create(
            course=course,
            title=module_data["title"],
            order=module_order,
            description=module_data["description"],
            learning_objectives=normalize_ai_learning_objectives(
                module_data["learning_objectives"]
            ),
            created_by=job.creator,
            updated_by=job.creator,
        )
        for lesson_order, lesson_data in enumerate(module_data["lessons"], 1):
            Lesson.objects.create(
                module=module,
                title=lesson_data["title"],
                order=lesson_order,
                learning_objectives=normalize_ai_learning_objectives(
                    lesson_data["learning_objectives"]
                ),
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
        lesson.learning_objectives = normalize_ai_learning_objectives(
            lesson_data["learning_objectives"]
        )
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
    publish_ai_generation_progress(
        job=job,
        payload={
            "type": "module_done",
            "module_index": module.order - 1,
            "stage": f"Generated module {module.order}.",
        },
    )


@transaction.atomic
def materialize_final_assessment(*, job, generated, standards):
    Assessment.objects.create(
        level=AssessmentLevel.COURSE,
        course=job.course,
        title=generated["title"],
        questions=generated["questions"],
        created_by=job.creator,
        updated_by=job.creator,
    )
    rescale_course_lesson_durations(course=job.course, standards=standards)
    course_service.recalculate_duration_estimate(course=job.course)
    publish_ai_generation_progress(
        job=job,
        payload={
            "type": "final_assessment_done",
            "stage": "Course details ready",
        },
    )


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


@transaction.atomic
def complete_job(
    *,
    job,
    stage,
    result=None,
    provider=None,
    model=None,
    input_tokens=None,
    output_tokens=None,
):
    """Atomically finish a job unless cancellation won the completion race."""

    job = AIGenerationJob.objects.select_for_update().get(pk=job.pk)
    if job.status in {
        AIGenerationStatus.COMPLETED,
        AIGenerationStatus.FAILED,
        AIGenerationStatus.CANCELLED,
    }:
        return False
    if job.cancel_requested:
        check_cancelled(job)
        return False

    job.status = AIGenerationStatus.COMPLETED
    job.stage = stage
    job.completed_at = timezone.now()
    update_fields = ["status", "stage", "completed_at", "updated_datetime"]
    for field, value in (
        ("result", result),
        ("provider", provider),
        ("model", model),
        ("input_tokens", input_tokens),
        ("output_tokens", output_tokens),
    ):
        if value is not None:
            setattr(job, field, value)
            update_fields.append(field)
    job.save(update_fields=update_fields)
    return True


@transaction.atomic
def cancel_job(*, job, actor):
    if job.creator_id != actor.id:
        raise exceptions.PermissionDenied()
    job = AIGenerationJob.objects.select_for_update().get(pk=job.pk)
    if job.status in {
        AIGenerationStatus.COMPLETED,
        AIGenerationStatus.FAILED,
        AIGenerationStatus.CANCELLED,
    }:
        return job
    job.cancel_requested = True
    job.save(update_fields=["cancel_requested", "updated_datetime"])
    if job.status == AIGenerationStatus.QUEUED:
        check_cancelled(job)
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
