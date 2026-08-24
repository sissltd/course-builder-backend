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


@extend_schema(tags=["MIE Developer — Submissions"])
class MieSubmissionIngestView(APIView):
    """Endpoint 1 - submit a course idea for the MIE pipeline.

    Authenticated with the developer's API key. The body is stored
    verbatim; the dedup engine decides the outcome immediately and a
    signed webhook event is recorded for every outcome, including
    short-circuits.
    """

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "mie_ingest"

    @extend_schema(
        summary="Submit a course idea",
        description=(
            "Receives one course idea (title plus any extra context keys). "
            "The title runs three sequential checks before anything is "
            "queued: previously rejected, duplicate of an existing course, "
            "duplicate already in queue. The response status tells you "
            "which outcome applied; a webhook event is fired immediately "
            "in all cases.\n\n"
            "Possible statuses: PENDING_REVIEW (queued for admin review), "
            "DUPLICATE_IN_QUEUE, DUPLICATE_EXISTING, PREVIOUSLY_REJECTED."
        ),
        request=SubmissionIngestSerializer,
        responses={
            status.HTTP_201_CREATED: OpenApiResponse(
                SubmissionIngestResponseSerializer,
                description=(
                    "Idea accepted and processed by the dedup engine. The "
                    "status field carries the outcome."
                ),
            ),
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(
                description="Missing/invalid title or malformed JSON.",
            ),
            status.HTTP_401_UNAUTHORIZED: OpenApiResponse(
                description="Missing/invalid API key, or account not active."
            ),
            status.HTTP_429_TOO_MANY_REQUESTS: OpenApiResponse(
                description="Rate limit exceeded; retry later."
            ),
        },
        examples=[
            OpenApiExample(
                "Queued",
                value={
                    "id": "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11",
                    "reference": "SCB-0d1c7b2e-P",
                    "status": "PENDING_REVIEW",
                },
                response_only=True,
            ),
            OpenApiExample(
                "Short-circuited as duplicate",
                value={
                    "id": "7a2b9c4d-1111-4a3f-9a2b-1f4e8c9d0a22",
                    "reference": "SCB-7a2b9c4d-E",
                    "status": "DUPLICATE_EXISTING",
                },
                response_only=True,
            ),
        ],
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


@extend_schema(tags=["MIE Developer — Submissions"])
class MieSubmissionQueueView(ListAPIView):
    """The developer's own submission queue.

    Rows are hard-scoped server-side to the authenticated developer -
    there is no developer filter parameter on this surface by design, so
    no query combination can expose another developer's data. Every
    pipeline state appears here, including dedup short-circuits.
    """

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
            "Your submission queue - every idea you have submitted, in "
            "every possible state, newest first. Filter with ?status= "
            "(one pipeline state) and ?search= (title substring). The "
            "reference suffix letter always reflects the current state."
        ),
        parameters=QUEUE_FILTER_PARAMETERS,
        responses={
            status.HTTP_200_OK: DevSubmissionSerializer(many=True),
            status.HTTP_401_UNAUTHORIZED: OpenApiResponse(
                description="Missing/invalid credentials or account not active."
            ),
        },
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)
