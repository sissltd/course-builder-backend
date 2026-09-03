from datetime import datetime

from drf_spectacular.utils import extend_schema
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.courses.enums import (
    AIGenerationKind,
    AIGenerationStatus,
    CourseStatus,
    MediaSource,
)
from api.courses.models import AIGenerationJob, CourseThumbnail
from api.courses.serializers.ai_generation_serializer import (
    AIAssistCreateSerializer,
    AIAssistApplyResponseSerializer,
    AICourseGenerationCreateSerializer,
    AIGenerationJobSerializer,
    AIThumbnailCreateSerializer,
    AIThumbnailApplyResponseSerializer,
)
from api.courses.services import ai_generation_service
from api.courses.tasks import (
    generate_ai_assist,
    generate_ai_course,
    generate_ai_thumbnail,
)
from api.users.permissions import IsCourseCreatorRole


class AICourseGenerationListCreateView(APIView):
    permission_classes = [IsCourseCreatorRole]

    @extend_schema(
        summary="Start AI course generation",
        description="Starts the two-phase creator AI course workflow.",
        request=AICourseGenerationCreateSerializer,
        responses={200: AIGenerationJobSerializer, 202: AIGenerationJobSerializer},
        tags=["Creator — AI course generation"],
    )
    def post(self, request):
        serializer = AICourseGenerationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job, created = ai_generation_service.create_course_job(
            creator=request.user, validated_data=serializer.validated_data
        )
        if created:
            transaction = generate_ai_course.delay(str(job.id))
            job.celery_task_id = transaction.id or ""
            job.save(update_fields=["celery_task_id", "updated_datetime"])
        return Response(
            AIGenerationJobSerializer(job).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )


class AIGenerationDetailView(APIView):
    permission_classes = [IsCourseCreatorRole]

    def get_object(self, request, pk):
        return AIGenerationJob.objects.prefetch_related("items").filter(
            pk=pk, creator=request.user
        ).first() or (_ for _ in ()).throw(exceptions.NotFound())

    @extend_schema(
        summary="Get AI course generation progress",
        request=None,
        responses={200: AIGenerationJobSerializer},
        tags=["Creator — AI course generation"],
    )
    def get(self, request, pk):
        return Response(AIGenerationJobSerializer(self.get_object(request, pk)).data)

    @extend_schema(
        summary="Cancel AI course generation",
        request=None,
        responses={202: AIGenerationJobSerializer},
        tags=["Creator — AI course generation"],
    )
    def delete(self, request, pk):
        job = ai_generation_service.cancel_job(
            job=self.get_object(request, pk), actor=request.user
        )
        return Response(
            AIGenerationJobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )


class AIAssistListCreateView(APIView):
    permission_classes = [IsCourseCreatorRole]

    @extend_schema(
        summary="Generate an AI field suggestion",
        request=AIAssistCreateSerializer,
        responses={202: AIGenerationJobSerializer},
        tags=["Creator — AI course generation"],
    )
    def post(self, request, course_pk):
        course = request.user.courses.filter(pk=course_pk).first()
        if not course:
            raise exceptions.NotFound()
        if course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "AI assists are only available for Draft courses."
            )
        serializer = AIAssistCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target = ai_generation_service.resolve_assist_target(
            course, data["target_type"], data["target_id"]
        )
        target_course_id = (
            target.id
            if data["target_type"] == "course"
            else (
                target.course_id
                if data["target_type"] == "module"
                else target.module.course_id
            )
        )
        if target_course_id != course.id:
            raise exceptions.PermissionDenied()
        job = AIGenerationJob.objects.create(
            creator=request.user,
            course=course,
            kind=AIGenerationKind.ASSIST,
            request_payload={
                **data,
                "target_id": str(data["target_id"]),
                "target_updated_at": data["target_updated_at"].isoformat(),
            },
        )
        generate_ai_assist.delay(str(job.id))
        return Response(
            AIGenerationJobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )


class AIAssistApplyView(APIView):
    permission_classes = [IsCourseCreatorRole]

    @extend_schema(
        summary="Apply an AI field suggestion",
        request=None,
        responses={200: AIAssistApplyResponseSerializer},
        tags=["Creator — AI course generation"],
    )
    def post(self, request, course_pk, pk):
        job = AIGenerationJob.objects.filter(
            pk=pk,
            course_id=course_pk,
            creator=request.user,
            kind=AIGenerationKind.ASSIST,
        ).first()
        if not job:
            raise exceptions.NotFound()
        if job.status != AIGenerationStatus.COMPLETED:
            raise exceptions.ValidationError("This suggestion is not ready to apply.")
        payload = job.request_payload
        target = ai_generation_service.resolve_assist_target(
            job.course, payload["target_type"], payload["target_id"]
        )
        if target.updated_datetime > datetime.fromisoformat(
            payload["target_updated_at"]
        ):
            return Response(
                {"detail": "The field changed after this suggestion was requested."},
                status=status.HTTP_409_CONFLICT,
            )
        allowed = {
            "course": {"title", "description", "learning_objectives", "tags"},
            "module": {"title", "description", "learning_objectives"},
            "lesson": {"title", "script", "learning_objectives"},
        }
        field = payload["field"]
        if field not in allowed[payload["target_type"]]:
            raise exceptions.ValidationError("This field cannot be updated with AI.")
        setattr(target, field, job.result["suggestion"])
        target.updated_by = request.user
        target.save(update_fields=[field, "updated_by", "updated_datetime"])
        return Response(
            {
                "field": field,
                "value": getattr(target, field),
                "updated_datetime": target.updated_datetime,
            }
        )


class AIThumbnailCreateView(APIView):
    permission_classes = [IsCourseCreatorRole]

    @extend_schema(
        summary="Generate an AI course thumbnail",
        request=AIThumbnailCreateSerializer,
        responses={202: AIGenerationJobSerializer},
        tags=["Creator — AI course generation"],
    )
    def post(self, request, course_pk):
        course = request.user.courses.filter(
            pk=course_pk, status=CourseStatus.DRAFT
        ).first()
        if not course:
            raise exceptions.NotFound()
        serializer = AIThumbnailCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = AIGenerationJob.objects.create(
            creator=request.user,
            course=course,
            kind=AIGenerationKind.THUMBNAIL,
            request_payload=serializer.validated_data,
        )
        generate_ai_thumbnail.delay(str(job.id))
        return Response(
            AIGenerationJobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )


class AIThumbnailApplyView(APIView):
    permission_classes = [IsCourseCreatorRole]

    @extend_schema(
        summary="Apply an AI course thumbnail",
        request=None,
        responses={200: AIThumbnailApplyResponseSerializer},
        tags=["Creator — AI course generation"],
    )
    def post(self, request, course_pk, pk):
        job = AIGenerationJob.objects.filter(
            pk=pk,
            course_id=course_pk,
            creator=request.user,
            kind=AIGenerationKind.THUMBNAIL,
            status=AIGenerationStatus.COMPLETED,
        ).first()
        if not job:
            raise exceptions.NotFound()
        CourseThumbnail.objects.filter(course_id=course_pk, is_active=True).update(
            is_active=False
        )
        thumbnail = CourseThumbnail.objects.create(
            course_id=course_pk,
            source=MediaSource.AI_GENERATED,
            external_url=job.result["url"],
            width=1536,
            height=1024,
            created_by=request.user,
            updated_by=request.user,
        )
        job.course.thumbnail_url = job.result["url"]
        job.course.save(update_fields=["thumbnail_url", "updated_datetime"])
        return Response({"id": thumbnail.id, "url": job.result["url"]})
