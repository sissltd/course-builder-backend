from datetime import datetime

from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.courses.exceptions import AIDispatchUnavailable
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
from includes.helpers.pagination import PageNumberAPIPagination
from shared.spectacular.responses import STANDARD_ERROR_RESPONSES, inline_error_response


AI_TAG = ["Creator — AI"]
JOB_EXAMPLE = {
    "id": "40d3e800-877a-48fd-9344-52f62af8d20c",
    "course": None,
    "kind": "FULL_COURSE",
    "status": "QUEUED",
    "stage": "Creating content...",
    "current_phase": "CREATING_CONTENT",
    "result": {},
    "error_message": "",
    "cancel_requested": False,
    "builder_ready": False,
    "items": [],
    "created_datetime": "2026-09-03T20:00:00Z",
    "updated_datetime": "2026-09-03T20:00:00Z",
}
AI_DISPATCH_UNAVAILABLE_RESPONSE = inline_error_response(
    description="The job was created but could not be handed to Celery.",
    examples=[
        OpenApiExample(
            name="AI dispatch unavailable",
            value={
                "errors": [
                    {
                        "type": "server_error",
                        "code": "ai_dispatch_unavailable",
                        "message": "AI generation could not be queued. Please try again.",
                        "field_name": None,
                    }
                ]
            },
        )
    ],
)
IN_PROGRESS_STATUS_PARAM = "in_progress"
LIST_STATUS_VALUES = [
    IN_PROGRESS_STATUS_PARAM,
    AIGenerationStatus.QUEUED.value,
    AIGenerationStatus.RUNNING.value,
    AIGenerationStatus.STRUCTURE_READY.value,
    AIGenerationStatus.COMPLETED.value,
    AIGenerationStatus.FAILED.value,
    AIGenerationStatus.CANCELLED.value,
]


def _dispatch_generation_task(*, task, job):
    try:
        async_result = task.delay(str(job.id))
    except Exception as exc:
        ai_generation_service.fail_job(
            job=job,
            message="AI generation could not be queued. Please try again.",
        )
        raise AIDispatchUnavailable() from exc
    job.celery_task_id = async_result.id or ""
    job.save(update_fields=["celery_task_id", "updated_datetime"])
    return job


class AICourseGenerationListCreateView(APIView):
    permission_classes = [IsCourseCreatorRole]
    pagination_class = PageNumberAPIPagination

    @extend_schema(
        operation_id="course_ai_generations_list",
        summary="List AI generation jobs",
        description=(
            "Returns creator-owned AI generation jobs so the frontend can resume "
            "a loading screen after navigation, refresh, or reconnect.\n\n"
            "Call this when the creator opens the Create with AI experience before "
            "starting a new request.\n\n"
            "**Auth:** Course Creator or invited Staff Writer with a valid Bearer token.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Defaults to `status=in_progress`, which returns `QUEUED`, "
            "`RUNNING`, and `STRUCTURE_READY` jobs. Stale in-flight jobs are marked "
            "`FAILED` before the list is returned, so the frontend can stop polling "
            "jobs that can no longer complete."
        ),
        request=None,
        parameters=[
            OpenApiParameter(
                name="status",
                location=OpenApiParameter.QUERY,
                required=False,
                type=str,
                enum=LIST_STATUS_VALUES,
                description=(
                    "Filter jobs by status. Defaults to `in_progress` for QUEUED, "
                    "RUNNING, and STRUCTURE_READY."
                ),
            ),
            OpenApiParameter(
                name="page",
                location=OpenApiParameter.QUERY,
                required=False,
                type=int,
                description="One-based results page. Defaults to 1.",
            ),
            OpenApiParameter(
                name="size",
                location=OpenApiParameter.QUERY,
                required=False,
                type=int,
                description="Rows per page. Defaults to 10.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=AIGenerationJobSerializer(many=True),
                description="Generation jobs matching the selected status.",
                examples=[
                    OpenApiExample(
                        name="In-progress jobs",
                        value={
                            "status": True,
                            "message": "Successfully retrieved data",
                            "data": {
                                "paginator": {
                                    "count": 1,
                                    "page": 1,
                                    "page_size": 10,
                                    "total_pages": 1,
                                    "next": None,
                                    "next_page_number": None,
                                    "previous": None,
                                    "previous_page_number": None,
                                },
                                "results": [JOB_EXAMPLE],
                            },
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["forbidden"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
        tags=AI_TAG,
    )
    def get(self, request):
        ai_generation_service.fail_stale_in_flight_jobs(creator=request.user)
        status_filter = request.query_params.get("status", IN_PROGRESS_STATUS_PARAM)
        if status_filter not in LIST_STATUS_VALUES:
            raise exceptions.ValidationError({"status": "Unsupported status filter."})
        queryset = AIGenerationJob.objects.prefetch_related("items").filter(
            creator=request.user
        )
        if status_filter == IN_PROGRESS_STATUS_PARAM:
            queryset = queryset.filter(
                status__in=ai_generation_service.IN_FLIGHT_JOB_STATUSES
            )
        else:
            queryset = queryset.filter(status=status_filter)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, self)
        serializer = AIGenerationJobSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        operation_id="course_ai_generations_create",
        summary="Start AI course generation",
        description=(
            "Starts an asynchronous, two-phase AI workflow that creates a Draft "
            "course, its modules and lessons, and all lesson, module, and final "
            "assessments. It returns a generation job immediately for the frontend "
            "to poll.\n\n"
            "Call this after the creator completes the Create with AI form and "
            "accepts the selected category terms.\n\n"
            "**Auth:** Course Creator or invited Staff Writer with a valid Bearer token.\n\n"
            "**Prerequisites:** The category must exist; when supplied, the topic "
            "must belong to that category.\n\n"
            "**Important:** Generation is asynchronous. Poll the returned job URL. "
            "Reuse `idempotency_key` when retrying the same submission; an existing "
            "job is returned with 200 instead of creating a duplicate."
        ),
        request=AICourseGenerationCreateSerializer,
        examples=[
            OpenApiExample(
                name="Create a practical data analysis course",
                request_only=True,
                value={
                    "title": "Practical Data Analysis for Beginners",
                    "description": "Teach new analysts to clean, explore, and present business data.",
                    "category": "6f9619ff-8b86-d011-b42d-00cf4fc964ff",
                    "topic": "7a1d8c95-7f34-4ef2-a6ab-b6e4fe273012",
                    "terms_accepted": True,
                    "idempotency_key": "course-ai-20260903-001",
                },
            )
        ],
        responses={
            200: OpenApiResponse(
                response=AIGenerationJobSerializer,
                description="The existing job for the supplied idempotency key.",
                examples=[OpenApiExample(name="Existing job", value=JOB_EXAMPLE)],
            ),
            202: OpenApiResponse(
                response=AIGenerationJobSerializer,
                description="A new AI generation job was queued.",
                examples=[OpenApiExample(name="Queued job", value=JOB_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["forbidden"],
            **STANDARD_ERROR_RESPONSES["rate_limited"],
            503: AI_DISPATCH_UNAVAILABLE_RESPONSE,
            **STANDARD_ERROR_RESPONSES["server"],
        },
        tags=AI_TAG,
    )
    def post(self, request):
        serializer = AICourseGenerationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job, created = ai_generation_service.create_course_job(
            creator=request.user, validated_data=serializer.validated_data
        )
        if created:
            job = _dispatch_generation_task(task=generate_ai_course, job=job)
        return Response(
            AIGenerationJobSerializer(job).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )


class AIGenerationDetailView(APIView):
    permission_classes = [IsCourseCreatorRole]

    def get_object(self, request, pk):
        ai_generation_service.fail_stale_in_flight_jobs(creator=request.user)
        return AIGenerationJob.objects.prefetch_related("items").filter(
            pk=pk, creator=request.user
        ).first() or (_ for _ in ()).throw(exceptions.NotFound())

    @extend_schema(
        operation_id="course_ai_generations_retrieve",
        summary="Retrieve AI course generation progress",
        description=(
            "Returns the latest state of a creator-owned AI generation job, including "
            "its active Figma phase and ordered progress items. The completed response "
            "contains the generated Draft course identifier.\n\n"
            "Poll this endpoint while the two generation screens are displayed.\n\n"
            "**Auth:** Course Creator or invited Staff Writer with a valid Bearer token.\n\n"
            "**Prerequisites:** A generation job must already have been created by the caller.\n\n"
            "**Important:** Poll every 2–3 seconds and stop on COMPLETED, FAILED, or "
            "CANCELLED. Jobs owned by another creator are returned as 404."
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                response=AIGenerationJobSerializer,
                description="The current generation state.",
                examples=[
                    OpenApiExample(name="Generation in progress", value=JOB_EXAMPLE)
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["forbidden"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
        tags=AI_TAG,
    )
    def get(self, request, pk):
        return Response(AIGenerationJobSerializer(self.get_object(request, pk)).data)

    @extend_schema(
        operation_id="course_ai_generations_cancel",
        summary="Cancel AI course generation",
        description=(
            "Requests cancellation of a creator-owned AI generation job. The worker "
            "stops at the next safe checkpoint and marks remaining progress items cancelled.\n\n"
            "Call this when the creator selects ‘Stop this process and go back’.\n\n"
            "**Auth:** Course Creator or invited Staff Writer with a valid Bearer token.\n\n"
            "**Prerequisites:** The job must belong to the caller.\n\n"
            "**Important:** Cancellation is cooperative, so the returned job may show "
            "`cancel_requested=true` before its status becomes CANCELLED. Repeating the "
            "request or cancelling a terminal job is safe."
        ),
        request=None,
        responses={
            202: OpenApiResponse(
                response=AIGenerationJobSerializer,
                description="Cancellation was accepted or the job was already terminal.",
                examples=[
                    OpenApiExample(
                        name="Cancellation requested",
                        value={**JOB_EXAMPLE, "cancel_requested": True},
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["forbidden"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
        tags=AI_TAG,
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
        description=(
            "Queues an AI suggestion for one editable field on a Draft course, module, "
            "or lesson without changing the saved value. The returned job can be polled "
            "until its result contains the suggestion.\n\n"
            "Call this from an AI-assist action beside a course-builder field.\n\n"
            "**Auth:** Course Creator or invited Staff Writer with a valid Bearer token.\n\n"
            "**Prerequisites:** The course must be a caller-owned Draft, and target_id "
            "must identify a record within that course.\n\n"
            "**Important:** Save `target_updated_at` from the builder. Applying the result "
            "later returns 409 if that field's record changed in the meantime."
        ),
        request=AIAssistCreateSerializer,
        examples=[
            OpenApiExample(
                name="Improve lesson objectives",
                request_only=True,
                value={
                    "target_type": "lesson",
                    "target_id": "1c7f03e0-d42b-4ec6-af42-e0d4eae83111",
                    "field": "learning_objectives",
                    "current_value": ["Understand data cleaning"],
                    "instruction": "Make these measurable and suitable for beginners.",
                    "target_updated_at": "2026-09-03T20:10:00Z",
                },
            )
        ],
        responses={
            202: OpenApiResponse(
                response=AIGenerationJobSerializer,
                description="The field-suggestion job was queued.",
                examples=[
                    OpenApiExample(
                        name="Suggestion queued",
                        value={**JOB_EXAMPLE, "kind": "ASSIST", "current_phase": None},
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["forbidden"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["rate_limited"],
            503: AI_DISPATCH_UNAVAILABLE_RESPONSE,
            **STANDARD_ERROR_RESPONSES["server"],
        },
        tags=AI_TAG,
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
        # Resolve (and 404 on) unknown or cross-course targets before the
        # single-flight check so a bad request never burns the creator's slot.
        ai_generation_service.resolve_assist_target(
            course, data["target_type"], data["target_id"]
        )
        job = ai_generation_service.create_short_job(
            creator=request.user,
            course=course,
            kind=AIGenerationKind.ASSIST,
            request_payload={
                **data,
                "target_id": str(data["target_id"]),
                "target_updated_at": data["target_updated_at"].isoformat(),
            },
        )
        job = _dispatch_generation_task(task=generate_ai_assist, job=job)
        return Response(
            AIGenerationJobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )


class AIAssistApplyView(APIView):
    permission_classes = [IsCourseCreatorRole]

    @extend_schema(
        summary="Apply an AI field suggestion",
        description=(
            "Applies a completed AI suggestion to its original course, module, or lesson "
            "field and returns the saved value and update time.\n\n"
            "Call this only after the creator previews and accepts the generated suggestion.\n\n"
            "**Auth:** Course Creator or invited Staff Writer with a valid Bearer token.\n\n"
            "**Prerequisites:** The assist job must belong to the caller, target this course, "
            "and have status COMPLETED.\n\n"
            "**Important:** The operation returns 409 rather than overwriting newer edits "
            "when the target changed after generation began."
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                response=AIAssistApplyResponseSerializer,
                description="The suggested value was saved.",
                examples=[
                    OpenApiExample(
                        name="Suggestion applied",
                        value={
                            "field": "learning_objectives",
                            "value": [
                                "Clean a small dataset using a repeatable checklist."
                            ],
                            "updated_datetime": "2026-09-03T20:15:00Z",
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["forbidden"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["conflict"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
        tags=AI_TAG,
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
        description=(
            "Queues generation of a thumbnail image for a caller-owned Draft course. "
            "It returns a job whose completed result contains the generated image URL.\n\n"
            "Call this from the thumbnail step after the course structure is available.\n\n"
            "**Auth:** Course Creator or invited Staff Writer with a valid Bearer token.\n\n"
            "**Prerequisites:** The course must exist, belong to the caller, and remain Draft.\n\n"
            "**Important:** This does not change the active course thumbnail. Poll the job, "
            "preview its result, then call the apply endpoint after creator approval."
        ),
        request=AIThumbnailCreateSerializer,
        examples=[
            OpenApiExample(
                name="Generate an analytics thumbnail",
                request_only=True,
                value={
                    "prompt": "A clean professional workspace with colorful business charts, navy and orange palette, no text"
                },
            )
        ],
        responses={
            202: OpenApiResponse(
                response=AIGenerationJobSerializer,
                description="The thumbnail-generation job was queued.",
                examples=[
                    OpenApiExample(
                        name="Thumbnail queued",
                        value={
                            **JOB_EXAMPLE,
                            "kind": "THUMBNAIL",
                            "current_phase": None,
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["forbidden"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["rate_limited"],
            503: AI_DISPATCH_UNAVAILABLE_RESPONSE,
            **STANDARD_ERROR_RESPONSES["server"],
        },
        tags=AI_TAG,
    )
    def post(self, request, course_pk):
        course = request.user.courses.filter(
            pk=course_pk, status=CourseStatus.DRAFT
        ).first()
        if not course:
            raise exceptions.NotFound()
        serializer = AIThumbnailCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = ai_generation_service.create_short_job(
            creator=request.user,
            course=course,
            kind=AIGenerationKind.THUMBNAIL,
            request_payload=serializer.validated_data,
        )
        job = _dispatch_generation_task(task=generate_ai_thumbnail, job=job)
        return Response(
            AIGenerationJobSerializer(job).data, status=status.HTTP_202_ACCEPTED
        )


class AIThumbnailApplyView(APIView):
    permission_classes = [IsCourseCreatorRole]

    @extend_schema(
        summary="Apply an AI course thumbnail",
        description=(
            "Makes a completed AI-generated image the active thumbnail for its Draft course "
            "and returns the created thumbnail record. Any previously active thumbnail is disabled.\n\n"
            "Call this after the creator previews and accepts the generated image.\n\n"
            "**Auth:** Course Creator or invited Staff Writer with a valid Bearer token.\n\n"
            "**Prerequisites:** The thumbnail job must belong to the caller, target this "
            "course, and have status COMPLETED.\n\n"
            "**Important:** This changes the active thumbnail immediately; applying another "
            "generated thumbnail later replaces it."
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                response=AIThumbnailApplyResponseSerializer,
                description="The generated image is now the active course thumbnail.",
                examples=[
                    OpenApiExample(
                        name="Thumbnail applied",
                        value={
                            "id": "d7a4fbf8-58cf-4f21-83d0-1d96fa35c902",
                            "url": "https://cdn.example.com/courses/data-analysis-thumbnail.png",
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["forbidden"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
        tags=AI_TAG,
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
