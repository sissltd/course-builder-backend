from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import APIView

from api.authorization import codenames
from api.authorization.permissions import Perm
from api.production.enums import ProductionRunStatus
from api.courses.enums import DistributionChannel
from api.production.serializers.admin_production_serializer import (
    ChannelMappingCreateSerializer,
    ChannelMappingPreviewRequestSerializer,
    ChannelMappingPreviewSerializer,
    ChannelMappingSerializer,
    DistributionRecordSerializer,
    DistributionSerializer,
    PackageSerializer,
    ProductionRunDetailSerializer,
    ProductionRunSerializer,
)
from api.production.services import channel_mapping_service, packaging_service, production_service
from includes.helpers.pagination import PageNumberAPIPagination
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES
from shared.spectacular.responses import inline_error_response

TAG = "Admin — Production Engine"

RUN_ID_PARAMETER = OpenApiParameter(
    name="run_id", type=OpenApiTypes.UUID, location=OpenApiParameter.PATH, description="Production run id."
)

RUN_EXAMPLE = {
    "id": "8f2c1d6e-3b4a-4c5d-9e8f-0a1b2c3d4e5f",
    "course": {"id": "3f9a2e11-6b7c-4d2a-9e5f-1c8d4a7b2f30", "title": "Intro to Python", "status": "AWAITING_VIDEO"},
    "requested_by": "writer@soludesk.com",
    "status": "COMPLETED",
    "status_reason": "",
    "quote_amount": "46.20",
    "budget_amount": "100.00",
    "spent_amount": "0.8421",
    "attempts": 1,
    "started_at": "2026-10-10T09:00:00Z",
    "finished_at": "2026-10-10T09:06:40Z",
    "created_datetime": "2026-10-10T08:59:58Z",
    "updated_datetime": "2026-10-10T09:06:40Z",
}


def _conflict(code: str, message: str) -> OpenApiExample:
    return OpenApiExample(
        code,
        value={"errors": [{"type": "client_error", "code": code, "message": message, "field_name": None}]},
    )


class ProductionRunListView(APIView):
    """Production Engine runs, newest first."""

    permission_classes = [Perm(codenames.MIE_VIEW_PIPELINE, codenames.PRODUCTION_MANAGE)]
    pagination_class = PageNumberAPIPagination

    @extend_schema(
        operation_id="admin_production_runs_list",
        summary="List production runs",
        description=(
            "Every Production Engine run, newest first: which course, its "
            "quote against the budget, what it has spent, and where it sits.\n\n"
            "Called by the APE Pipeline screen's runs table.\n\n"
            "**Auth:** `mie.view_pipeline` (View APE Pipeline: Admin, Super "
            "Admin by default) or `production.manage`.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** A run is created when a course starts waiting on "
            "engine-made video. `BLOCKED` runs need an admin: either the quote "
            "or the spend reached the per-course budget. Rows are paginated "
            "under `data.results`."
        ),
        tags=[TAG],
        parameters=[
            OpenApiParameter(
                "status", str, enum=ProductionRunStatus.values, required=False, description="Only runs in this status."
            ),
            OpenApiParameter("course", OpenApiTypes.UUID, required=False, description="Only this course's runs."),
            OpenApiParameter("page", int, required=False, description="Page number."),
            OpenApiParameter("size", int, required=False, description="Rows per page."),
        ],
        responses={
            200: OpenApiResponse(
                response=ProductionRunSerializer(many=True),
                description="A page of runs.",
                examples=[OpenApiExample("Runs", value=[RUN_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        status_filter = request.query_params.get("status") or None
        if status_filter and status_filter not in ProductionRunStatus.values:
            raise exceptions.ValidationError({"status": [f"Must be one of {ProductionRunStatus.values}."]})
        course_filter = request.query_params.get("course") or None
        if course_filter:
            import uuid

            try:
                uuid.UUID(course_filter)
            except ValueError as exc:
                raise exceptions.ValidationError({"course": ["Must be a course id."]}) from exc
        queryset = production_service.runs_queryset(status=status_filter, course_id=course_filter)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, self)
        return paginator.get_paginated_response(ProductionRunSerializer(page, many=True).data)


class ProductionRunDetailView(APIView):
    """One run, with each lesson's progress."""

    permission_classes = [Perm(codenames.MIE_VIEW_PIPELINE, codenames.PRODUCTION_MANAGE)]

    @extend_schema(
        operation_id="admin_production_runs_retrieve",
        summary="Show a production run",
        description=(
            "One run with every lesson of its course and how far the engine "
            "got with each (`scene_count` 0 means not planned yet).\n\n"
            "Called when an admin opens a run from the pipeline screen.\n\n"
            "**Auth:** `mie.view_pipeline` or `production.manage`.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** 404 for an unknown run."
        ),
        tags=[TAG],
        parameters=[RUN_ID_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=ProductionRunDetailSerializer,
                description="The run.",
                examples=[
                    OpenApiExample(
                        "Run",
                        value={
                            **RUN_EXAMPLE,
                            "lessons": [
                                {
                                    "lesson_id": "c0ffee00-0000-4000-8000-000000000001",
                                    "title": "Variables",
                                    "scene_count": 9,
                                }
                            ],
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, run_id):
        run = production_service.get_run(run_id=run_id)
        run.lessons = production_service.lesson_progress(run)
        return Response(ProductionRunDetailSerializer(run).data)


class ProductionRunRetryView(APIView):
    permission_classes = [Perm(codenames.PRODUCTION_MANAGE)]

    @extend_schema(
        summary="Retry a production run",
        description=(
            "Sends a failed or budget-blocked run back to the queue. It is "
            "quoted again against the budget in force now.\n\n"
            "Called from a failed or blocked run, after the cause is fixed "
            "(e.g. the budget was raised).\n\n"
            "**Auth:** `production.manage` (Admin, Super Admin by default).\n\n"
            "**Prerequisites:** The run is `FAILED` or `BLOCKED`, and its new "
            "quote fits the per-course budget.\n\n"
            "**Important:** Work already done is kept: storyboards, narration, "
            "frames and finished lesson videos whose inputs did not change are "
            "reused, so a retry pays only for what is left. Nothing starts while "
            "`production_enabled` is off; the run waits in the queue. Takes no body."
        ),
        tags=[TAG],
        parameters=[RUN_ID_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(
                response=ProductionRunSerializer,
                description="The run, queued again.",
                examples=[OpenApiExample("Queued", value={**RUN_EXAMPLE, "status": "QUEUED"})],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            409: inline_error_response(
                description="The run is not failed or blocked, or still does not fit the budget.",
                examples=[
                    _conflict("production_run_conflict", "Only failed or blocked runs can be retried; this one is completed."),
                    _conflict(
                        "production_over_budget",
                        "The quote of $151.80 is above the per-course budget of $100.00. Raise the budget, then retry.",
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, run_id):
        run = production_service.retry_run(run=production_service.get_run(run_id=run_id), actor=request.user)
        return Response(ProductionRunSerializer(run).data)


class ProductionRunCancelView(APIView):
    permission_classes = [Perm(codenames.PRODUCTION_MANAGE)]

    @extend_schema(
        summary="Cancel a production run",
        description=(
            "Stops a queued, running or blocked run. A running run stops "
            "before its next step.\n\n"
            "Called when a course should not be produced by the engine.\n\n"
            "**Auth:** `production.manage` (Admin, Super Admin by default).\n\n"
            "**Prerequisites:** The run is `QUEUED`, `RUNNING` or `BLOCKED`.\n\n"
            "**Important:** The course keeps waiting for its video; a new run "
            "can be requested later. Money already spent is not refunded. "
            "Takes no body."
        ),
        tags=[TAG],
        parameters=[RUN_ID_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(
                response=ProductionRunSerializer,
                description="The cancelled run.",
                examples=[OpenApiExample("Cancelled", value={**RUN_EXAMPLE, "status": "CANCELLED"})],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            409: inline_error_response(
                description="The run has already finished.",
                examples=[_conflict("production_run_conflict", "A completed run cannot be cancelled.")],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, run_id):
        run = production_service.cancel_run(run=production_service.get_run(run_id=run_id), actor=request.user)
        return Response(ProductionRunSerializer(run).data)


MAPPING_ID_PARAMETER = OpenApiParameter(
    name="mapping_id", type=OpenApiTypes.UUID, location=OpenApiParameter.PATH, description="Channel mapping id."
)
COURSE_ID_PARAMETER = OpenApiParameter(
    name="course_id", type=OpenApiTypes.UUID, location=OpenApiParameter.PATH, description="Course id."
)

MAPPING_EXAMPLE = {
    "id": "5b1e2c3d-4f5a-4b6c-8d7e-9f0a1b2c3d4e",
    "channel": "UDEMY",
    "version": 2,
    "delivery_method": "UPLOAD_KIT",
    "target_schema": {"type": "object", "required": ["title"], "properties": {"title": {"type": "string", "maxLength": 60}}},
    "field_map": {"title": {"from": "course.title", "transform": ["truncate:60"]}},
    "response_id_path": "",
    "is_active": True,
    "notes": "Udemy raised the subtitle limit.",
    "created_by": "admin@soludesk.com",
    "created_datetime": "2026-10-12T09:00:00Z",
}

FIELD_MAP_GUIDE = (
    "`field_map` maps each target field path (dots nest: `price.amount`) to a "
    "rule: `{\"from\": \"course.title\"}` reads the canonical package (with "
    "optional `transform` list and `default`); `{\"const\": value}` is fixed; "
    "`{\"each\": \"modules\", \"map\": {...}}` maps a list, its `from` paths "
    "starting at the item (or the root with a leading `/`). Transforms: "
    "truncate:N, upper, lower, strip, join:SEP, count, first_sentence, "
    "minutes_to_seconds, string, number, integer. The package has `course`, "
    "`modules[].lessons[]`, `final_quiz`, `pricing`, `disclosure`, and the "
    "channel's own price row as `channel`."
)


class ChannelMappingListCreateView(APIView):
    """Every version of every channel's mapping; saving a new version."""

    def get_permissions(self):
        if self.request.method == "POST":
            return [Perm(codenames.PRODUCTION_MANAGE)()]
        return [Perm(codenames.MIE_VIEW_PIPELINE, codenames.PRODUCTION_MANAGE)()]

    @extend_schema(
        operation_id="admin_production_channel_mappings_list",
        summary="List channel mappings",
        description=(
            "Every version of each distribution channel's mapping, newest "
            "version first per channel; one per channel is active.\n\n"
            "Called by the Production Engine's channel settings screen.\n\n"
            "**Auth:** `mie.view_pipeline` or `production.manage`.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Version 1 of each channel is seeded. SoluDesk's v1 "
            "is a draft until SoluDesk's course schema is shared."
        ),
        tags=[TAG],
        parameters=[OpenApiParameter("channel", str, enum=DistributionChannel.values, required=False, description="Only this channel.")],
        responses={
            200: OpenApiResponse(
                response=ChannelMappingSerializer(many=True),
                description="The mappings.",
                examples=[OpenApiExample("Mappings", value=[MAPPING_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        channel = request.query_params.get("channel") or None
        if channel and channel not in DistributionChannel.values:
            raise exceptions.ValidationError({"channel": [f"Must be one of {DistributionChannel.values}."]})
        return Response(ChannelMappingSerializer(channel_mapping_service.mappings_queryset(channel=channel), many=True).data)

    @extend_schema(
        operation_id="admin_production_channel_mappings_create",
        summary="Save a channel mapping version",
        description=(
            "Saves a new version of a channel's mapping: the channel's JSON "
            "Schema and how each of its fields is filled from the course "
            "package. Versions are never edited.\n\n"
            "Called when a platform's schema arrives or changes.\n\n"
            "**Auth:** `production.manage` (Admin, Super Admin by default).\n\n"
            "**Prerequisites:** `target_schema` is valid JSON Schema and every "
            "`field_map` rule is well-formed.\n\n"
            "**Important:** " + FIELD_MAP_GUIDE + " With `activate`, the new "
            "version replaces the active one immediately; preview it against a "
            "course first. API pushes read the channel's URL and key from the "
            "server environment, never from the mapping."
        ),
        tags=[TAG],
        request=ChannelMappingCreateSerializer,
        examples=[
            OpenApiExample(
                "Udemy v2",
                request_only=True,
                value={
                    "channel": "UDEMY",
                    "delivery_method": "UPLOAD_KIT",
                    "target_schema": MAPPING_EXAMPLE["target_schema"],
                    "field_map": MAPPING_EXAMPLE["field_map"],
                    "notes": "Udemy raised the subtitle limit.",
                    "activate": False,
                },
            )
        ],
        responses={
            201: OpenApiResponse(
                response=ChannelMappingSerializer,
                description="The saved version.",
                examples=[OpenApiExample("Saved", value=MAPPING_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = ChannelMappingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mapping = channel_mapping_service.create_version(actor=request.user, **serializer.validated_data)
        return Response(ChannelMappingSerializer(mapping).data, status=201)


class ChannelMappingActivateView(APIView):
    permission_classes = [Perm(codenames.PRODUCTION_MANAGE)]

    @extend_schema(
        summary="Activate a channel mapping version",
        description=(
            "Makes this version the one its channel's deliveries use.\n\n"
            "Called after previewing a saved version, or to roll back.\n\n"
            "**Auth:** `production.manage`.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Idempotent. Takes no body. 404 for an unknown mapping."
        ),
        tags=[TAG],
        parameters=[MAPPING_ID_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(
                response=ChannelMappingSerializer,
                description="The now-active version.",
                examples=[OpenApiExample("Active", value=MAPPING_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, mapping_id):
        mapping = channel_mapping_service.activate(
            mapping=channel_mapping_service.get_mapping(mapping_id=mapping_id), actor=request.user
        )
        return Response(ChannelMappingSerializer(mapping).data)


class ChannelMappingPreviewView(APIView):
    permission_classes = [Perm(codenames.PRODUCTION_MANAGE)]

    @extend_schema(
        summary="Preview a channel mapping on a course",
        description=(
            "Shapes a course with this mapping version and lists every gap "
            "against the channel's schema. Nothing is delivered or saved.\n\n"
            "Called before activating a new version.\n\n"
            "**Auth:** `production.manage`.\n\n"
            "**Prerequisites:** None; any course can be previewed.\n\n"
            "**Important:** Media links in the payload are signed for 7 days. "
            "404 for an unknown mapping or course."
        ),
        tags=[TAG],
        parameters=[MAPPING_ID_PARAMETER],
        request=ChannelMappingPreviewRequestSerializer,
        examples=[OpenApiExample("Preview", request_only=True, value={"course_id": "3f9a2e11-6b7c-4d2a-9e5f-1c8d4a7b2f30"})],
        responses={
            200: OpenApiResponse(
                response=ChannelMappingPreviewSerializer,
                description="The payload and its gaps.",
                examples=[
                    OpenApiExample(
                        "Gaps",
                        value={"payload": {"title": "Intro to Python"}, "gaps": ["lecture_count: 4 is less than the minimum of 5"]},
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, mapping_id):
        mapping = channel_mapping_service.get_mapping(mapping_id=mapping_id)
        serializer = ChannelMappingPreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = production_service.get_course(course_id=serializer.validated_data["course_id"])
        payload, gaps = channel_mapping_service.preview(mapping=mapping, course=course)
        return Response(ChannelMappingPreviewSerializer({"payload": payload, "gaps": gaps}).data)


class CoursePackageView(APIView):
    """A published course's package downloads; building it again."""

    def get_permissions(self):
        if self.request.method == "POST":
            return [Perm(codenames.PRODUCTION_MANAGE)()]
        return [Perm(codenames.MIE_VIEW_PIPELINE, codenames.PRODUCTION_MANAGE)()]

    @extend_schema(
        operation_id="admin_production_course_package_retrieve",
        summary="Get a course's package downloads",
        description=(
            "The newest canonical package, SCORM 1.2 and 2004 exports and "
            "upload kits of a course, and each lesson's video and captions, "
            "as fresh download links.\n\n"
            "Called by the course's package page, and when uploading a kit.\n\n"
            "**Auth:** `mie.view_pipeline` or `production.manage`.\n\n"
            "**Prerequisites:** None; `files` is empty until the course has "
            "been packaged.\n\n"
            "**Important:** Links last 10 minutes; fetch again for new ones. "
            "404 for an unknown course."
        ),
        tags=[TAG],
        parameters=[COURSE_ID_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=PackageSerializer,
                description="Downloads.",
                examples=[
                    OpenApiExample(
                        "Package",
                        value={
                            "files": [{"kind": "SCORM_2004", "channel": "", "size_bytes": 81234567, "built_at": "2026-10-12T10:05:00Z", "url": "https://..."}],
                            "lessons": [{"lesson_id": "c0ffee00-0000-4000-8000-000000000001", "title": "Variables", "video_url": "https://...", "captions_vtt_url": "https://...", "captions_srt_url": "https://..."}],
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, course_id):
        course = production_service.get_course(course_id=course_id)
        return Response(PackageSerializer(packaging_service.package_downloads(course=course)).data)

    @extend_schema(
        operation_id="admin_production_course_package_create",
        summary="Package a course again",
        description=(
            "Queues a PACKAGE run: the course's package and SCORM exports are "
            "rebuilt where anything changed, and every channel not yet live is "
            "delivered again.\n\n"
            "Called after configuring a channel (e.g. setting SOLUDESK_API_URL) "
            "or activating a new mapping version.\n\n"
            "**Auth:** `production.manage`.\n\n"
            "**Prerequisites:** The course is `PUBLISHED`.\n\n"
            "**Important:** Idempotent while a run is active (that run is "
            "returned). Channels already live are left alone. Takes no body."
        ),
        tags=[TAG],
        parameters=[COURSE_ID_PARAMETER],
        request=None,
        responses={
            201: OpenApiResponse(
                response=ProductionRunSerializer,
                description="The queued PACKAGE run.",
                examples=[OpenApiExample("Queued", value={**RUN_EXAMPLE, "kind": "PACKAGE", "status": "QUEUED", "quote_amount": "0.00"})],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            409: inline_error_response(
                description="The course is not published.",
                examples=[_conflict("production_run_conflict", "Only a published course can be packaged.")],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, course_id):
        run = production_service.request_packaging(course_id=course_id, actor=request.user)
        return Response(ProductionRunSerializer(run).data, status=201)


class DistributionPublishedView(APIView):
    permission_classes = [Perm(codenames.PRODUCTION_MANAGE)]

    @extend_schema(
        summary="Record a manual channel upload",
        description=(
            "Marks a course live on a channel after a person uploaded its kit "
            "(Udemy, Coursera), recording the channel's own course id.\n\n"
            "Called once the upload is done on the channel's site.\n\n"
            "**Auth:** `production.manage`.\n\n"
            "**Prerequisites:** The course is `PUBLISHED`.\n\n"
            "**Important:** Idempotent for the same id; a different id "
            "replaces it. The course then shows the channel in the public "
            "catalogue. 404 for an unknown distribution."
        ),
        tags=[TAG],
        parameters=[
            OpenApiParameter(
                name="distribution_id", type=OpenApiTypes.UUID, location=OpenApiParameter.PATH, description="Course distribution id."
            )
        ],
        request=DistributionRecordSerializer,
        examples=[OpenApiExample("Udemy", request_only=True, value={"external_course_id": "5123456"})],
        responses={
            200: OpenApiResponse(
                response=DistributionSerializer,
                description="The distribution, now live.",
                examples=[
                    OpenApiExample(
                        "Live",
                        value={
                            "id": "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d",
                            "channel": "UDEMY",
                            "status": "PUBLISHED",
                            "external_course_id": "5123456",
                            "failure_reason": "",
                            "published_at": "2026-10-13T14:00:00Z",
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            409: inline_error_response(
                description="The course is not published.",
                examples=[_conflict("production_run_conflict", "Only a published course can be recorded as live on a channel.")],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, distribution_id):
        serializer = DistributionRecordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        distribution = packaging_service.record_publication(
            distribution_id=distribution_id,
            external_course_id=serializer.validated_data["external_course_id"],
            actor=request.user,
        )
        return Response(DistributionSerializer(distribution).data)
