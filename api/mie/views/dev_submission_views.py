from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import status
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.mie.authentication import MieDeveloperAuthentication
from api.mie.enums import SubmissionStatus
from api.mie.models import CourseSubmission
from api.mie.permissions import IsMieDeveloper
from api.mie.serializers.dev_submission_serializer import DevSubmissionSerializer
from api.mie.serializers.submission_serializer import (
    SubmissionIngestResponseSerializer,
    SubmissionIngestSerializer,
)
from api.mie.services import submission_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

QUEUE_FILTER_PARAMETERS = [
    OpenApiParameter(
        name="status",
        type=str,
        enum=SubmissionStatus.values,
        required=False,
        description="Restrict the queue to one pipeline state.",
    ),
    OpenApiParameter(
        name="search",
        type=str,
        required=False,
        description="Case-insensitive substring match on the idea title.",
    ),
]


@extend_schema(tags=["Developer — MIE Submissions"])
class MieSubmissionIngestView(APIView):
    """Endpoint 1 - submit a course idea for the MIE pipeline."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "mie_ingest"

    @extend_schema(
        summary="Submit a course idea",
        description=(
            "Receives one course idea (title plus any extra context keys) and "
            "runs it through a three-stage deduplication engine before deciding "
            "whether to queue it for admin review. The response status indicates "
            "which outcome applied: new submission queued, duplicate already in "
            "queue, duplicate matching an existing course, or previously "
            "rejected.\n\n"

            "Called by a developer integration whenever it has a new course "
            "idea to submit into the MIE pipeline.\n\n"

            "**Auth:** Requires a valid MIE developer API key.\n\n"

            "**Prerequisites:** The developer account must be in ACTIVE "
            "status (approved by a superadmin).\n\n"

            "**Important:** A signed webhook event is fired immediately for "
            "every outcome, including short-circuits. The dedup checks are "
            "sequential and non-idempotent — resubmitting the same title may "
            "produce a different result if queue contents have changed."
        ),
        request=SubmissionIngestSerializer,
        examples=[
            OpenApiExample(
                name="New course idea",
                request_only=True,
                value={
                    "title": "Introduction to Machine Learning with Python",
                },
            ),
        ],
        responses={
            status.HTTP_201_CREATED: OpenApiResponse(
                SubmissionIngestResponseSerializer,
                description=(
                    "Idea accepted and processed by the dedup engine. The "
                    "status field carries the outcome."
                ),
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["rate_limited"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        if not isinstance(request.data, dict):
            return Response(
                {"title": ["Submission body must be a JSON object."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = SubmissionIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submission, _queued = submission_service.submit_idea(
            developer=request.auth, payload=dict(request.data)
        )
        return Response(
            SubmissionIngestResponseSerializer(
                submission, context={"request": request}
            ).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Developer — MIE Submissions"])
class MieSubmissionQueueView(ListAPIView):
    """The developer's own submission queue."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]
    serializer_class = DevSubmissionSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status"]
    search_fields = ["title"]
    ordering = ["-created_datetime"]

    def get_queryset(self):
        return CourseSubmission.objects.filter(developer=self.request.auth)

    @extend_schema(
        summary="List your submissions",
        description=(
            "Returns the authenticated developer's complete submission queue: "
            "every idea they have submitted in every pipeline state, ordered "
            "newest first. Supports filtering by pipeline state and title "
            "substring search. The reference suffix letter always reflects "
            "the current state.\n\n"

            "Called when a developer opens their submissions dashboard or "
            "needs to check the status of previously submitted ideas.\n\n"

            "**Auth:** Requires a valid MIE developer API key.\n\n"

            "**Prerequisites:** The developer account must be in ACTIVE "
            "status (approved by a superadmin).\n\n"

            "**Important:** Results are scoped server-side to the "
            "authenticated developer — no query parameter can expose another "
            "developer's submissions. Every pipeline state appears here, "
            "including dedup short-circuits."
        ),
        parameters=QUEUE_FILTER_PARAMETERS,
        responses={
            status.HTTP_200_OK: DevSubmissionSerializer(many=True),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)
