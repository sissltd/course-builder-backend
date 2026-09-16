from django.shortcuts import get_object_or_404
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import exceptions, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.courses.enums import DifficultyLevel
from api.mie.models import CourseSubmission, SubmissionRejectionReason
from api.mie.serializers.admin_submission_serializer import SubmissionDecisionSerializer
from api.mie.services import submission_admin_service
from api.operations.filters import MieRecommendationFilterSet
from api.operations.serializers import (
    AdminAnalyticsSerializer,
    MieBulkDecisionSerializer,
    MieRecommendationRowSerializer,
    MieRecommendationsSerializer,
    PipelineOverviewSerializer,
    SystemHealthSerializer,
)
from api.operations.services import (
    analytics_service,
    health_service,
    pipeline_service,
    recommendation_service,
)
from api.users.permissions import CanDecideMieIdeas, IsAdminOrSuperAdminRole
from includes.helpers.pagination import PageNumberAPIPagination
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES
from shared.response.success import custom_success_response

_NO_DATA_NOTE = (
    "Metrics with nothing recorded behind them yet return **null**, not "
    "zero, so a tile can tell 'no data' apart from a real reading of "
    "nothing. Render null as an empty state rather than 0."
)


@extend_schema(tags=["Admin — System Health"])
class SystemHealthView(APIView):
    """Uptime and latency per monitored dependency."""

    permission_classes = [IsAuthenticated, IsAdminOrSuperAdminRole]
    serializer_class = SystemHealthSerializer  # schema generation only

    @extend_schema(
        summary="Retrieve system health",
        description=(
            "Returns per-service uptime, latency and current status over a "
            "rolling window, plus the summary tiles above the table.\n\n"
            "Called when the admin System Health screen loads.\n\n"
            "**Auth:** Admin or Super Admin.\n\n"
            "**Prerequisites:** Services must be registered and something "
            "must be writing `ServiceHealthSample` rows — without a probe "
            "feeding it, every service reports null uptime.\n\n"
            f"**Important:** {_NO_DATA_NOTE} Uptime is computed from "
            "samples in the window, never stored, so it cannot go stale. A "
            "service with no samples reports null rather than 100%."
        ),
        parameters=[
            OpenApiParameter(
                name="window_days",
                type=int,
                required=False,
                description=(
                    "Rolling window to measure over. Defaults to "
                    f"{health_service.DEFAULT_WINDOW_DAYS}."
                ),
            )
        ],
        responses={
            200: OpenApiResponse(
                response=SystemHealthSerializer,
                description="Per-service health over the window.",
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        try:
            window = int(
                request.query_params.get(
                    "window_days", health_service.DEFAULT_WINDOW_DAYS
                )
            )
        except (TypeError, ValueError):
            window = health_service.DEFAULT_WINDOW_DAYS
        window = max(1, min(window, 365))

        data = health_service.get_system_health(window_days=window)
        return Response(SystemHealthSerializer(data).data)


@extend_schema(tags=["Admin — APE Pipeline"])
class PipelineOverviewView(APIView):
    """Production funnel and provider load."""

    permission_classes = [IsAuthenticated, IsAdminOrSuperAdminRole]
    serializer_class = PipelineOverviewSerializer  # schema generation only

    @extend_schema(
        summary="Retrieve the production pipeline overview",
        description=(
            "Returns the AI production funnel: job counts per stage, the "
            "active/queued/completed/failed tiles, and last-known load and "
            "queue depth for each external provider.\n\n"
            "Called when the admin APE Pipeline screen loads.\n\n"
            "**Auth:** Admin or Super Admin.\n\n"
            "**Prerequisites:** None — an empty pipeline returns every "
            "stage at zero.\n\n"
            f"**Important:** {_NO_DATA_NOTE} Every stage is always present "
            "in funnel order so the chart keeps its shape. Provider load "
            "and queue are last-known readings, not live: use "
            "`readings_updated_at` to judge staleness."
        ),
        responses={
            200: OpenApiResponse(
                response=PipelineOverviewSerializer,
                description="Funnel counts and provider readings.",
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        return Response(
            PipelineOverviewSerializer(pipeline_service.get_pipeline_overview()).data
        )


@extend_schema(tags=["Admin — Analytics"])
class AdminAnalyticsView(APIView):
    """Catalogue, enrolment, cost, distribution and KPI figures."""

    permission_classes = [IsAuthenticated, IsAdminOrSuperAdminRole]
    serializer_class = AdminAnalyticsSerializer  # schema generation only

    @extend_schema(
        summary="Retrieve admin analytics",
        description=(
            "Returns the Analytics screen in one call: catalogue size, "
            "enrolment and completion, production cost with a daily series "
            "and a category split, channel distribution, produced vs "
            "approved vs rejected, and the KPI scorecard.\n\n"
            "Called when the admin Analytics screen loads, and again "
            "whenever the period selector changes.\n\n"
            "**Auth:** Admin or Super Admin.\n\n"
            "**Prerequisites:** Enrolment and cost figures need "
            "`Enrollment` and `ProductionCost` rows; without them those "
            "tiles are null while the rest still report.\n\n"
            f"**Important:** {_NO_DATA_NOTE} Money is returned as decimal "
            "strings, not floats, so no precision is lost in transit. An "
            "unknown `period` falls back to the default rather than "
            "erroring."
        ),
        parameters=[
            OpenApiParameter(
                name="period",
                type=str,
                enum=list(analytics_service.PERIODS),
                required=False,
                description=(
                    "Window to report over, matching the screen's selector. "
                    f"Defaults to `{analytics_service.DEFAULT_PERIOD}`."
                ),
            )
        ],
        responses={
            200: OpenApiResponse(
                response=AdminAnalyticsSerializer,
                description="Analytics for the selected period.",
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        data = analytics_service.get_analytics(
            period=request.query_params.get(
                "period", analytics_service.DEFAULT_PERIOD
            )
        )
        return Response(AdminAnalyticsSerializer(data).data)


_RECOMMENDATION_FILTERS = [
    OpenApiParameter(
        name="search",
        type=str,
        required=False,
        description="Case-insensitive substring match on the idea title.",
    ),
    OpenApiParameter(
        name="category",
        type=str,
        required=False,
        description="Platform category id, from `GET /api/v1/categories/`.",
    ),
    OpenApiParameter(
        name="difficulty_level",
        type=str,
        required=False,
        enum=DifficultyLevel.values,
        description="Difficulty the submitter claimed. An unknown value is a 400.",
    ),
    OpenApiParameter(
        name="min_demand_score",
        type=int,
        required=False,
        description=(
            "Keep ideas scored at least this high. Unscored ideas drop out "
            "whenever this is set, since they have no score to compare."
        ),
    ),
    OpenApiParameter(
        name="submitted_after",
        type=str,
        required=False,
        description="ISO-8601 lower bound on when the idea arrived.",
    ),
    OpenApiParameter(
        name="submitted_before",
        type=str,
        required=False,
        description="ISO-8601 upper bound on when the idea arrived.",
    ),
]

_DECIDER_AUTH = (
    "**Auth:** Writer or Super Admin. A plain Admin is refused — deciding "
    "ideas is the Writer's job, and the Super Admin keeps it so the queue is "
    "never blocked.\n\n"
)


@extend_schema(tags=["Admin — MIE Recommendations"])
class MieRecommendationsView(APIView):
    """The Recommendations screen: pending ideas, ranked and filterable."""

    permission_classes = [CanDecideMieIdeas]
    pagination_class = PageNumberAPIPagination
    serializer_class = MieRecommendationsSerializer  # schema generation only

    @extend_schema(
        summary="List MIE recommendations",
        description=(
            "Returns course ideas still awaiting review — the Recommendations "
            "table — ranked by the market-intelligence signals recorded on "
            "them: demand score first, then estimated monthly earnings. Each "
            "row carries what the table draws: topic, category, difficulty, "
            "demand score and monthly searches, plus the description behind "
            "the Topic details panel.\n\n"
            "Called when the screen loads, and again on every filter or page "
            "change.\n\n"
            + _DECIDER_AUTH
            + "**Prerequisites:** None. Ideas appear here while they are in "
            "PENDING_REVIEW; scores are set through `signals` on the MIE "
            "admin submissions endpoint.\n\n"
            "**Important:** Rows are paginated under `data.results`, with "
            "`data.paginator` alongside. `pending_total` and `scored_total` "
            "describe the **filtered** set, so a category selection narrows "
            "them too; compare them to show scoring coverage. Unscored ideas "
            "sort last rather than being hidden, unless `min_demand_score` "
            "is set, which drops them."
        ),
        parameters=_RECOMMENDATION_FILTERS,
        responses={
            200: OpenApiResponse(
                response=MieRecommendationsSerializer,
                description="Ranked recommendations plus scoring coverage.",
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        filterset = MieRecommendationFilterSet(
            request.query_params,
            queryset=recommendation_service.recommendation_queryset(),
        )
        if not filterset.is_valid():
            raise exceptions.ValidationError(filterset.errors)

        queryset = filterset.qs
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, self)
        response = paginator.get_paginated_response(
            MieRecommendationRowSerializer(page, many=True).data
        )
        # The coverage counts ride inside `data`, beside paginator and results,
        # so one response answers "what is on this page" and "how much of the
        # filtered queue is scored".
        response.data["data"].update(
            recommendation_service.recommendation_totals(queryset)
        )
        return response


@extend_schema(tags=["Admin — MIE Recommendations"])
class MieRecommendationApproveView(APIView):
    """Accept one idea from the Recommendations table."""

    permission_classes = [CanDecideMieIdeas]

    @extend_schema(
        summary="Approve an idea",
        description=(
            "Accepts one course idea, taking it out of the Recommendations "
            "queue and notifying the submitter.\n\n"
            "Called from the row's Approve button, or Approve topic in the "
            "Topic details panel.\n\n"
            + _DECIDER_AUTH
            + "**Prerequisites:** The idea must exist.\n\n"
            "**Important:** Fires a SUBMISSION_APPROVED webhook to the "
            "submitter immediately, and is reversible — rejecting it later "
            "flips it back and fires the matching webhook. Approving does "
            "not create a course or pay anyone: production is a separate, "
            "unbuilt step."
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                response=MieRecommendationRowSerializer,
                description="The idea, now approved.",
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, id):
        submission = get_object_or_404(CourseSubmission, id=id)
        decided = submission_admin_service.decide_submission(
            actor=request.user, submission=submission, approve=True
        )
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Topic successfully approved.",
            data=MieRecommendationRowSerializer(decided).data,
        )


@extend_schema(tags=["Admin — MIE Recommendations"])
class MieRecommendationRejectView(APIView):
    """Decline one idea from the Recommendations table."""

    permission_classes = [CanDecideMieIdeas]

    @extend_schema(
        summary="Reject an idea",
        description=(
            "Declines one course idea with a reason from the shared "
            "taxonomy, so the submitter gets consistent feedback.\n\n"
            "Called from the row's Reject button, or Reject topic in the "
            "Topic details panel.\n\n"
            + _DECIDER_AUTH
            + "**Prerequisites:** The idea must exist, and "
            "`rejection_reason` must match an active rejection reason label "
            "from `GET /api/v1/mie/admin/rejection-reasons/`.\n\n"
            "**Important:** Fires a SUBMISSION_REJECTED webhook carrying the "
            "reason and note. Reversible — approving later flips it back. "
            "Rejecting enough of one crawler's ideas trips the circuit "
            "breaker and suspends that account."
        ),
        request=SubmissionDecisionSerializer,
        examples=[
            OpenApiExample(
                "Reject with a reason",
                request_only=True,
                value={
                    "rejection_reason": "Duplicate of existing catalog",
                    "rejection_note": "Covered by the live Kubernetes course.",
                },
            )
        ],
        responses={
            200: OpenApiResponse(
                response=MieRecommendationRowSerializer,
                description="The idea, now rejected.",
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, id):
        submission = get_object_or_404(CourseSubmission, id=id)
        serializer = SubmissionDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        label = serializer.validated_data.get("rejection_reason")
        if not label:
            raise exceptions.ValidationError(
                {"rejection_reason": ["A rejection reason label is required to reject."]}
            )
        reason = get_object_or_404(
            SubmissionRejectionReason, label=label, is_active=True
        )

        decided = submission_admin_service.decide_submission(
            actor=request.user,
            submission=submission,
            approve=False,
            rejection_reason=reason,
            rejection_note=serializer.validated_data.get("rejection_note", ""),
        )
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Topic successfully rejected.",
            data=MieRecommendationRowSerializer(decided).data,
        )


@extend_schema(tags=["Admin — MIE Recommendations"])
class MieRecommendationBulkDecisionView(APIView):
    """Decide a checkbox selection from the Recommendations table."""

    permission_classes = [CanDecideMieIdeas]

    @extend_schema(
        summary="Approve or reject selected ideas",
        description=(
            "Applies one decision to every selected idea — the bulk bar "
            "under the table's checkboxes.\n\n"
            "Called from Approve topic or Reject topic once rows are "
            "selected.\n\n"
            + _DECIDER_AUTH
            + "**Prerequisites:** Every id must exist. Rejecting needs a "
            "`rejection_reason` label, which is applied to the whole "
            "selection.\n\n"
            "**Important:** All or nothing — an id matching no idea returns "
            "404 and nothing is written, so a selection is never "
            "half-applied. Each idea still gets its own webhook. At most "
            f"{submission_admin_service.BULK_DECISION_LIMIT} ideas per call."
        ),
        request=MieBulkDecisionSerializer,
        examples=[
            OpenApiExample(
                "Approve a selection",
                request_only=True,
                value={
                    "ids": [
                        "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11",
                        "7c9e6679-7425-40de-944b-e07fc1f90ae7",
                    ],
                    "action": "approve",
                },
            ),
            OpenApiExample(
                "Reject a selection",
                request_only=True,
                value={
                    "ids": ["0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11"],
                    "action": "reject",
                    "rejection_reason": "Duplicate of existing catalog",
                },
            ),
        ],
        responses={
            200: OpenApiResponse(
                description="How many ideas were decided, and the rows.",
                examples=[
                    OpenApiExample(
                        "Approved five",
                        value={
                            "status": 200,
                            "success": True,
                            "message": "You have approved 5 topics.",
                            "data": {"decided": 5, "results": []},
                        },
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
    def post(self, request):
        serializer = MieBulkDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        approve = serializer.validated_data["action"] == "approve"

        reason = None
        if not approve:
            reason = get_object_or_404(
                SubmissionRejectionReason,
                label=serializer.validated_data["rejection_reason"],
                is_active=True,
            )

        decided = submission_admin_service.decide_submissions_bulk(
            actor=request.user,
            submission_ids=serializer.validated_data["ids"],
            approve=approve,
            rejection_reason=reason,
            rejection_note=serializer.validated_data.get("rejection_note", ""),
        )
        verb = "approved" if approve else "rejected"
        plural = "topic" if len(decided) == 1 else "topics"
        return custom_success_response(
            status=status.HTTP_200_OK,
            message=f"You have {verb} {len(decided)} {plural}.",
            data={
                "decided": len(decided),
                "results": MieRecommendationRowSerializer(decided, many=True).data,
            },
        )
