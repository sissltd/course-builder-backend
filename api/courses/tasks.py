from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.utils import timezone

from api.courses.ai import get_course_ai_provider
from api.courses.ai.providers import AIProviderError, AIProviderRateLimited
from api.courses.enums import AIGenerationItemStatus, AIGenerationStatus
from api.courses.models import AIGenerationJob
from api.courses.services import ai_generation_service
from shared.services.storage_service import StorageService

# One generation performs several sequential provider calls, each bounded by
# the provider adapter's 180s HTTP timeout. The soft limit exists so a stalled
# run is failed (and recorded on the job) instead of holding a worker slot
# forever; the hard limit is the unforgivable-kill fallback just after it.
COURSE_TASK_SOFT_TIME_LIMIT_SECONDS = 1800  # 30 min
COURSE_TASK_HARD_TIME_LIMIT_SECONDS = 2100  # 35 min
SHORT_TASK_SOFT_TIME_LIMIT_SECONDS = 300  # 5 min
SHORT_TASK_HARD_TIME_LIMIT_SECONDS = 330  # 5.5 min

AI_TASK_MAX_RETRIES = 3


def _fail_job(*, job, exc):
    """Record a terminal failure on the job and its in-flight progress items."""

    job.status = AIGenerationStatus.FAILED
    job.stage = "Generation failed"
    job.error_message = str(exc)[:4000]
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
    job.items.filter(status=AIGenerationItemStatus.RUNNING).update(
        status=AIGenerationItemStatus.FAILED, error_message=str(exc)[:4000]
    )


def _start_job(*, job, task_id, stage):
    """Move a queued or resumed job into its active polling state."""

    fields = ["status", "stage", "updated_datetime"]
    job.status = AIGenerationStatus.RUNNING
    job.stage = stage
    if task_id:
        job.celery_task_id = task_id
        fields.append("celery_task_id")
    if job.started_at is None:
        job.started_at = timezone.now()
        fields.append("started_at")
    job.save(update_fields=fields)


def _reschedule_for_provider_capacity(*, task, job, exc):
    """Release the worker until the local provider budget opens again."""

    job.stage = "Waiting for AI provider capacity..."
    job.save(update_fields=["stage", "updated_datetime"])
    async_result = task.apply_async(
        args=(str(job.id),), countdown=exc.retry_after_seconds
    )
    job.celery_task_id = async_result.id or ""
    job.save(update_fields=["celery_task_id", "updated_datetime"])


def _retry_provider_failure(*, task, job, exc):
    """Retry only transient provider failures; client errors are terminal."""

    if not exc.retryable:
        _fail_job(job=job, exc=exc)
        raise exc
    try:
        raise task.retry(
            exc=exc, countdown=min(120, 10 * (2**task.request.retries))
        )
    except MaxRetriesExceededError:
        _fail_job(job=job, exc=exc)
        raise exc


@shared_task(
    bind=True,
    max_retries=AI_TASK_MAX_RETRIES,
    soft_time_limit=COURSE_TASK_SOFT_TIME_LIMIT_SECONDS,
    time_limit=COURSE_TASK_HARD_TIME_LIMIT_SECONDS,
)
def generate_ai_course(self, job_id):
    job = AIGenerationJob.objects.select_related("creator", "course").get(pk=job_id)
    _start_job(
        job=job, task_id=self.request.id or "", stage="Creating content..."
    )
    if ai_generation_service.check_cancelled(job):
        return

    provider = get_course_ai_provider()
    job.provider = provider.name
    job.model = getattr(provider, "text_model", "")
    job.save(update_fields=["provider", "model", "updated_datetime"])
    payload = job.request_payload
    try:
        course = job.course
        if course is None:
            ai_generation_service.mark_item(
                job, "content_objectives", AIGenerationItemStatus.RUNNING
            )
            outline, usage = provider.generate_course_outline(
                title=payload["course_title"],
                description=payload["description"],
                category=payload["category_name"],
                topic=payload.get("topic_name", ""),
            )
            ai_generation_service.mark_item(
                job, "content_objectives", AIGenerationItemStatus.COMPLETED
            )
            if ai_generation_service.check_cancelled(job):
                return
            ai_generation_service.mark_item(
                job, "content_outlines", AIGenerationItemStatus.RUNNING
            )
            course = ai_generation_service.materialize_structure(
                job=job, generated=outline
            )
            ai_generation_service.add_usage(job=job, usage=usage)
            ai_generation_service.mark_item(
                job, "content_outlines", AIGenerationItemStatus.COMPLETED
            )
            ai_generation_service.mark_item(
                job, "content_lessons", AIGenerationItemStatus.COMPLETED
            )

        job.stage = "Preparing course details..."
        job.save(update_fields=["stage", "updated_datetime"])
        ai_generation_service.mark_item(
            job, "details_outlines", AIGenerationItemStatus.COMPLETED
        )
        ai_generation_service.mark_item(
            job, "details_modules", AIGenerationItemStatus.COMPLETED
        )
        ai_generation_service.mark_item(
            job, "details_lessons", AIGenerationItemStatus.RUNNING
        )
        if ai_generation_service.check_cancelled(job):
            return

        modules = course.modules.order_by("order").prefetch_related("lessons")
        for module in modules:
            if ai_generation_service.check_cancelled(job):
                return
            if ai_generation_service.module_content_is_materialized(module=module):
                continue
            generated, module_usage = provider.generate_module_content(
                course=course, module=module
            )
            ai_generation_service.materialize_module_content(
                job=job, module=module, generated=generated
            )
            ai_generation_service.add_usage(job=job, usage=module_usage)

        if ai_generation_service.check_cancelled(job):
            return
        if not ai_generation_service.final_assessment_is_materialized(course=course):
            final_assessment, final_usage = provider.generate_final_assessment(
                course=course
            )
            ai_generation_service.materialize_final_assessment(
                job=job, generated=final_assessment
            )
            ai_generation_service.add_usage(job=job, usage=final_usage)
        ai_generation_service.mark_item(
            job, "details_lessons", AIGenerationItemStatus.COMPLETED
        )
        job.status = AIGenerationStatus.COMPLETED
        job.stage = "Course details ready"
        job.result = {"course_id": str(course.id), "builder_ready": True}
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "stage",
                "result",
                "completed_at",
                "updated_datetime",
            ]
        )
    except AIProviderRateLimited as exc:
        _reschedule_for_provider_capacity(task=self, job=job, exc=exc)
    except AIProviderError as exc:
        _retry_provider_failure(task=self, job=job, exc=exc)
    except Exception as exc:
        _fail_job(job=job, exc=exc)
        raise


@shared_task(
    bind=True,
    max_retries=AI_TASK_MAX_RETRIES,
    soft_time_limit=SHORT_TASK_SOFT_TIME_LIMIT_SECONDS,
    time_limit=SHORT_TASK_HARD_TIME_LIMIT_SECONDS,
)
def generate_ai_assist(self, job_id):
    job = AIGenerationJob.objects.select_related("course", "creator").get(pk=job_id)
    _start_job(job=job, task_id=self.request.id or "", stage="Thinking content...")
    if ai_generation_service.check_cancelled(job):
        return
    payload = job.request_payload
    provider = get_course_ai_provider()
    try:
        suggestion, usage = provider.generate_assist(
            target=payload["field"],
            current_value=payload.get("current_value", ""),
            instruction=payload["instruction"],
            context={"title": job.course.title, "description": job.course.description},
        )
        job.status = AIGenerationStatus.COMPLETED
        job.stage = "Ready to apply"
        job.provider = provider.name
        job.model = getattr(provider, "text_model", "")
        job.result = {"suggestion": suggestion}
        job.input_tokens = usage.get("input_tokens", 0)
        job.output_tokens = usage.get("output_tokens", 0)
        job.completed_at = timezone.now()
        job.save()
    except AIProviderRateLimited as exc:
        _reschedule_for_provider_capacity(task=self, job=job, exc=exc)
    except AIProviderError as exc:
        # Transient provider failure: hand the job back to Celery for a
        # bounded, backed-off retry while it stays RUNNING - the frontend is
        # polling and must never see a false FAILED that then recovers. Once
        # retries are exhausted the job is failed below for real.
        _retry_provider_failure(task=self, job=job, exc=exc)
    except Exception as exc:
        _fail_job(job=job, exc=exc)
        raise


@shared_task(
    bind=True,
    max_retries=AI_TASK_MAX_RETRIES,
    soft_time_limit=SHORT_TASK_SOFT_TIME_LIMIT_SECONDS,
    time_limit=SHORT_TASK_HARD_TIME_LIMIT_SECONDS,
)
def generate_ai_thumbnail(self, job_id):
    job = AIGenerationJob.objects.select_related("course").get(pk=job_id)
    _start_job(job=job, task_id=self.request.id or "", stage="Creating thumbnail...")
    provider = get_course_ai_provider()
    try:
        image = provider.generate_thumbnail(prompt=job.request_payload["prompt"])
        key = StorageService.upload_bytes(
            image,
            folder=f"courses/{job.course_id}/thumbnails",
            content_type="image/png",
            acl="public-read",
        )
        job.status = AIGenerationStatus.COMPLETED
        job.stage = "Ready to apply"
        job.result = {"file_key": key, "url": StorageService.public_url(key)}
        job.provider = provider.name
        job.model = getattr(provider, "image_model", "")
        job.completed_at = timezone.now()
        job.save()
    except AIProviderRateLimited as exc:
        _reschedule_for_provider_capacity(task=self, job=job, exc=exc)
    except AIProviderError as exc:
        # Same bounded-retry contract as generate_ai_assist: stay RUNNING
        # across retries, fail only when they are exhausted.
        _retry_provider_failure(task=self, job=job, exc=exc)
    except Exception as exc:
        _fail_job(job=job, exc=exc)
        raise
