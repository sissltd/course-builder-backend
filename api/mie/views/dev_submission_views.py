from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import exceptions, status
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from api.mie.authentication import MieDeveloperAuthentication
from api.mie.enums import SubmissionStatus
from api.mie.models import CourseSubmission
from api.mie.models.course_submission import CONFIDENCE_NOTE_MAX_LENGTH
from api.mie.permissions import IsMieDeveloper
from api.mie.serializers.dev_submission_serializer import DevSubmissionSerializer
from api.mie.serializers.submission_serializer import (
    SubmissionIngestResponseSerializer,
    SubmissionIngestSerializer,
)
from api.mie.services import submission_service
from api.mie.throttling import MieDeveloperRateThrottle
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

DAILY_CAP_DETAIL = "Daily submission cap reached for this system account."

# Two limits answer 429 on ingest, so the shared rate_limited bucket is
# extended with the daily-cap case rather than replaced by a hand-written one.
INGEST_RATE_LIMITED_RESPONSES = {
    status.HTTP_429_TOO_MANY_REQUESTS: OpenApiResponse(
        description=(
            "Too many submissions. Two limits answer 429: the per-minute "
            "ingest rate limit, which applies to every caller, and - for "
            "platform-owned SYSTEM accounts only - the rolling 24-hour "
            "submission cap. Either way the `Retry-After` header carries "
            "the number of seconds to wait."
        ),
        examples=[
            *STANDARD_ERROR_RESPONSES["rate_limited"][
                status.HTTP_429_TOO_MANY_REQUESTS
            ].examples,
            OpenApiExample(
                name="Daily submission cap reached",
                value={
                    "errors": [
                        {
                            "type": "client_error",
                            "code": "throttled",
                            "message": (
                                f"{DAILY_CAP_DETAIL} Expected available in "
                                "5400 seconds."
                            ),
                            "field_name": None,
                        }
                    ]
                },
            ),
        ],
    ),
}


@extend_schema(tags=["Developer — MIE Submissions"])
class MieSubmissionIngestView(APIView):
    """Endpoint 1 - submit a course idea for the MIE pipeline."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]
    throttle_classes = [MieDeveloperRateThrottle]
    throttle_scope = "mie_ingest"

    @extend_schema(
        summary="Submit a course idea",
        description=(
            "Receives one course idea (title plus any extra context keys) and "
            "runs it through a three-stage deduplication engine before deciding "
            "whether to queue it for admin review. The response status indicates "
            "which outcome applied: new submission queued, duplicate already in "
            "queue, duplicate matching an existing course, or previously "
            "rejected. An optional `confidence_note` is lifted out alongside "
            "the title so reviewers see the evidence as its own field.\n\n"

            "Called by a developer integration whenever it has a new course "
            "idea to submit into the MIE pipeline.\n\n"

            "**Auth:** Requires a valid MIE developer API key.\n\n"

            "**Prerequisites:** The developer account must be in APPROVED "
            "status (approved by a superadmin).\n\n"

            "**Important:** A signed webhook event is fired immediately for "
            "every outcome, including short-circuits. The dedup checks are "
            "sequential and non-idempotent — resubmitting the same title may "
            "produce a different result if queue contents have changed. "
            "`confidence_note`, when sent, must be a string of at most "
            f"{CONFIDENCE_NOTE_MAX_LENGTH} characters. Platform-owned SYSTEM "
            "accounts (the MIE crawler) are also held to a rolling 24-hour "
            "submission cap that counts every outcome, dedup short-circuits "
            "included: past it nothing is stored and the endpoint returns "
            "429 with Retry-After set to when the oldest counted submission "
            "leaves the window. External developer accounts are never "
            "subject to that cap."
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
            OpenApiExample(
                name="Idea with a confidence note",
                request_only=True,
                value={
                    "title": "Kubernetes Cost Optimisation for Startups",
                    "description": (
                        "Right-sizing clusters, spot capacity and autoscaling "
                        "for teams without a platform engineer."
                    ),
                    "confidence_note": (
                        "940 job postings mention Kubernetes cost work this "
                        "month, up 34% on last month."
                    ),
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
            **INGEST_RATE_LIMITED_RESPONSES,
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
        try:
            submission, _queued = submission_service.submit_idea(
                developer=request.auth, payload=dict(request.data)
            )
        except submission_service.DailySubmissionCapReached as exc:
            # The same 429 + Retry-After the per-minute throttle answers
            # with, so a client that already honours one honours both.
            raise exceptions.Throttled(
                wait=exc.retry_after_seconds, detail=DAILY_CAP_DETAIL
            ) from exc
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

            "**Prerequisites:** The developer account must be in APPROVED "
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
