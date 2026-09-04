from celery import shared_task
from django.utils import timezone

from api.courses.ai import get_course_ai_provider
from api.courses.enums import AIGenerationItemStatus, AIGenerationStatus
from api.courses.models import AIGenerationJob
from api.courses.services import ai_generation_service
from shared.services.storage_service import StorageService


@shared_task(bind=True)
def generate_ai_course(self, job_id):
    job = AIGenerationJob.objects.select_related("creator").get(pk=job_id)
    job.celery_task_id = self.request.id or ""
    job.status = AIGenerationStatus.RUNNING
    job.stage = "Creating content..."
    job.started_at = timezone.now()
    job.save(
        update_fields=[
            "celery_task_id",
            "status",
            "stage",
            "started_at",
            "updated_datetime",
        ]
    )
    if ai_generation_service.check_cancelled(job):
        return

    provider = get_course_ai_provider()
    job.provider = provider.name
    job.model = getattr(provider, "text_model", "")
    job.save(update_fields=["provider", "model", "updated_datetime"])
    ai_generation_service.mark_item(
        job, "content_objectives", AIGenerationItemStatus.RUNNING
    )
    payload = job.request_payload
    try:
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
        course = ai_generation_service.materialize_structure(job=job, generated=outline)
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

        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        modules = course.modules.order_by("order").prefetch_related("lessons")
        for module in modules:
            if ai_generation_service.check_cancelled(job):
                return
            generated, module_usage = provider.generate_module_content(
                course=course, module=module
            )
            ai_generation_service.materialize_module_content(
                job=job, module=module, generated=generated
            )
            input_tokens += module_usage.get("input_tokens", 0)
            output_tokens += module_usage.get("output_tokens", 0)

        if ai_generation_service.check_cancelled(job):
            return
        final_assessment, final_usage = provider.generate_final_assessment(
            course=course
        )
        ai_generation_service.materialize_final_assessment(
            job=job, generated=final_assessment
        )
        input_tokens += final_usage.get("input_tokens", 0)
        output_tokens += final_usage.get("output_tokens", 0)
        ai_generation_service.mark_item(
            job, "details_lessons", AIGenerationItemStatus.COMPLETED
        )
        job.status = AIGenerationStatus.COMPLETED
        job.stage = "Course details ready"
        job.result = {"course_id": str(course.id), "builder_ready": True}
        job.input_tokens = input_tokens
        job.output_tokens = output_tokens
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "stage",
                "result",
                "input_tokens",
                "output_tokens",
                "completed_at",
                "updated_datetime",
            ]
        )
    except Exception as exc:
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
        raise


@shared_task
def generate_ai_assist(job_id):
    job = AIGenerationJob.objects.select_related("course", "creator").get(pk=job_id)
    job.status = AIGenerationStatus.RUNNING
    job.stage = "Thinking content..."
    job.started_at = timezone.now()
    job.save(update_fields=["status", "stage", "started_at", "updated_datetime"])
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
    except Exception as exc:
        job.status = AIGenerationStatus.FAILED
        job.error_message = str(exc)[:4000]
        job.completed_at = timezone.now()
        job.save()
        raise


@shared_task
def generate_ai_thumbnail(job_id):
    job = AIGenerationJob.objects.select_related("course").get(pk=job_id)
    job.status = AIGenerationStatus.RUNNING
    job.stage = "Creating thumbnail..."
    job.started_at = timezone.now()
    job.save()
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
    except Exception as exc:
        job.status = AIGenerationStatus.FAILED
        job.error_message = str(exc)[:4000]
        job.completed_at = timezone.now()
        job.save()
        raise
