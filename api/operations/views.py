from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.operations.serializers import (
    AdminAnalyticsSerializer,
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
from api.users.permissions import IsAdminOrSuperAdminRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

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


@extend_schema(tags=["Admin — MIE Recommendations"])
class MieRecommendationsView(APIView):
    """Highest-demand MIE ideas still awaiting a decision."""

    permission_classes = [IsAuthenticated, IsAdminOrSuperAdminRole]
    serializer_class = MieRecommendationsSerializer  # schema generation only

    @extend_schema(
        summary="Retrieve MIE recommendations",
        description=(
            "Returns partner-submitted course ideas still awaiting review, "
            "ranked by the market-intelligence signals admins record on "
            "them — demand score first, then estimated monthly earnings.\n\n"
            "Called when the admin MIE Recommendation screen loads.\n\n"
            "**Auth:** Admin or Super Admin.\n\n"
            "**Prerequisites:** Ideas must be in PENDING_REVIEW; scores are "
            "set via `POST /api/v1/mie/admin/submissions/{id}/signals/`.\n\n"
            "**Important:** Read-only over the MIE queue — deciding an idea "
            "still goes through the MIE admin endpoints. Unscored ideas "
            "sort last rather than being hidden, so a scoring backlog is "
            "visible; compare `scored_total` against `pending_total` to see "
            "how much of the queue has been assessed."
        ),
        parameters=[
            OpenApiParameter(
                name="limit",
                type=int,
                required=False,
                description=(
                    "How many ideas to return. Defaults to "
                    f"{recommendation_service.DEFAULT_LIMIT}, capped at "
                    f"{recommendation_service.MAX_LIMIT}."
                ),
            )
        ],
        responses={
            200: OpenApiResponse(
                response=MieRecommendationsSerializer,
                description="Ranked recommendations plus scoring coverage.",
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        try:
            limit = int(
                request.query_params.get(
                    "limit", recommendation_service.DEFAULT_LIMIT
                )
            )
        except (TypeError, ValueError):
            limit = recommendation_service.DEFAULT_LIMIT

        data = recommendation_service.get_recommendations(limit=limit)
        return Response(MieRecommendationsSerializer(data).data)
