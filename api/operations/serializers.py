from rest_framework import serializers


# ── System Health ────────────────────────────────────────────────────


class ServiceHealthRowSerializer(serializers.Serializer):
    """One row of the System Health table."""

    id = serializers.UUIDField()
    name = serializers.CharField(help_text="Service display name.")
    priority = serializers.CharField(help_text="How urgent degradation here is.")
    status = serializers.CharField(
        allow_null=True,
        help_text="Latest probe result, or null if never sampled in the window.",
    )
    uptime_percent = serializers.FloatField(
        allow_null=True,
        help_text=(
            "Share of samples that were operational. Null when the service "
            "has no samples in the window — unknown, not healthy."
        ),
    )
    avg_latency_ms = serializers.IntegerField(
        allow_null=True, help_text="Mean round-trip time, null if never measured."
    )
    sample_count = serializers.IntegerField(
        help_text="Probes recorded in the window, so the reader can judge confidence."
    )
    last_recovery_seconds = serializers.IntegerField(
        allow_null=True,
        help_text=(
            "Seconds this service took to return to operational after its "
            "most recent failure. Null when it has not failed in the "
            "window, or is still down - a service that has not recovered "
            "has no recovery time."
        ),
    )


class SystemHealthSerializer(serializers.Serializer):
    """The System Health screen."""

    window_days = serializers.IntegerField(help_text="Length of the measured window.")
    overall_uptime_percent = serializers.FloatField(
        allow_null=True, help_text="Mean uptime across services that have samples."
    )
    avg_api_latency_ms = serializers.IntegerField(
        allow_null=True, help_text="Mean latency across services that reported one."
    )
    avg_recovery_seconds = serializers.IntegerField(
        allow_null=True,
        help_text=(
            "Mean time-to-recovery across services that failed and came "
            "back in the window. Null when nothing has recovered."
        ),
    )
    degraded_count = serializers.IntegerField()
    down_count = serializers.IntegerField()
    services = ServiceHealthRowSerializer(many=True)


# ── APE Pipeline ─────────────────────────────────────────────────────


class PipelineStageRowSerializer(serializers.Serializer):
    """One stage of the production funnel."""

    stage = serializers.CharField()
    label = serializers.CharField(help_text="Human-readable stage name.")
    total = serializers.IntegerField()
    active = serializers.IntegerField(help_text="Queued or running.")
    completed = serializers.IntegerField()
    failed = serializers.IntegerField(help_text="Failed or retrying.")


class PipelineProviderRowSerializer(serializers.Serializer):
    """One external generation provider's last known readings."""

    id = serializers.UUIDField()
    name = serializers.CharField()
    kind = serializers.CharField()
    load_percent = serializers.IntegerField(
        allow_null=True, help_text="Last known utilisation, null if never polled."
    )
    queue_depth = serializers.IntegerField(allow_null=True)
    readings_updated_at = serializers.DateTimeField(
        allow_null=True,
        help_text="When load/queue were last written, so staleness is visible.",
    )


class PipelineOverviewSerializer(serializers.Serializer):
    """The APE Pipeline screen."""

    active_jobs = serializers.IntegerField()
    queue_depth = serializers.IntegerField()
    completed_today = serializers.IntegerField()
    failed_or_retrying = serializers.IntegerField()
    avg_pipeline_seconds = serializers.IntegerField(
        allow_null=True, help_text="Mean completed-job duration, null if none finished."
    )
    stages = PipelineStageRowSerializer(
        many=True, help_text="Every stage in funnel order, zeroes included."
    )
    providers = PipelineProviderRowSerializer(many=True)


# ── Analytics ────────────────────────────────────────────────────────


class AnalyticsCatalogSerializer(serializers.Serializer):
    total_catalog = serializers.IntegerField()
    published = serializers.IntegerField()
    created_in_period = serializers.IntegerField()


class AnalyticsEnrollmentSerializer(serializers.Serializer):
    total_enrollment = serializers.IntegerField()
    enrolled_in_period = serializers.IntegerField()
    completed = serializers.IntegerField()
    avg_completion_rate = serializers.FloatField(
        allow_null=True,
        help_text="Mean progress across enrolments. Null when there are none.",
    )


class AnalyticsDailyCostSerializer(serializers.Serializer):
    date = serializers.DateField()
    amount = serializers.CharField(help_text="Decimal as a string, to avoid float drift.")


class AnalyticsCostCategorySerializer(serializers.Serializer):
    category = serializers.CharField()
    amount = serializers.CharField()


class AnalyticsCostSerializer(serializers.Serializer):
    overall_cost = serializers.CharField(allow_null=True)
    cost_in_period = serializers.CharField(allow_null=True)
    cost_per_course = serializers.CharField(
        allow_null=True, help_text="Overall spend divided by published courses."
    )
    daily = AnalyticsDailyCostSerializer(many=True)
    by_category = AnalyticsCostCategorySerializer(many=True)


class AnalyticsDistributionSerializer(serializers.Serializer):
    channel = serializers.CharField()
    label = serializers.CharField()
    count = serializers.IntegerField()


class AnalyticsProductionSerializer(serializers.Serializer):
    produced = serializers.IntegerField()
    approved = serializers.IntegerField()
    rejected = serializers.IntegerField()


class AnalyticsEarningsSerializer(serializers.Serializer):
    total_earnings = serializers.CharField(
        allow_null=True,
        help_text="Creator earnings held across every wallet, as a decimal string.",
    )


class AnalyticsKpiSerializer(serializers.Serializer):
    """The KPI scorecard. Each figure is null until something backs it."""

    daily_output = serializers.FloatField(
        help_text="Courses produced per day across the period."
    )
    first_pass_approval_percent = serializers.FloatField(
        allow_null=True, help_text="Share of decisions that were approvals."
    )
    avg_pipeline_time_minutes = serializers.FloatField(
        allow_null=True, help_text="Mean completed-job duration. Null if none finished."
    )
    cost_per_course = serializers.CharField(
        allow_null=True, help_text="Lifetime spend over published courses."
    )
    review_turnaround_hours = serializers.FloatField(
        allow_null=True, help_text="Mean hours from submission to first decision."
    )
    system_uptime_percent = serializers.FloatField(
        allow_null=True, help_text="Mean uptime across sampled services."
    )
    targets = serializers.DictField(
        child=serializers.CharField(),
        help_text=(
            "Target shown beside each KPI, keyed by the figure it belongs "
            "to. Served with the data so the client does not hardcode "
            "business goals."
        ),
    )


class AdminAnalyticsSerializer(serializers.Serializer):
    """The Analytics screen.

    Metrics with nothing behind them yet are null rather than zero, so a
    tile can distinguish "no data" from "a real reading of nothing".
    """

    period = serializers.CharField()
    since = serializers.DateTimeField()
    catalog = AnalyticsCatalogSerializer()
    enrollment = AnalyticsEnrollmentSerializer()
    cost = AnalyticsCostSerializer()
    earnings = AnalyticsEarningsSerializer()
    distribution = AnalyticsDistributionSerializer(many=True)
    production_vs_approval = AnalyticsProductionSerializer()
    kpis = AnalyticsKpiSerializer()


# ── MIE Recommendations ──────────────────────────────────────────────


class MieRecommendationRowSerializer(serializers.Serializer):
    """One recommendable course idea from the MIE queue."""

    id = serializers.UUIDField()
    reference = serializers.CharField(
        help_text="MIE public reference; the suffix letter tracks status."
    )
    title = serializers.CharField()
    developer_email = serializers.EmailField(help_text="Submitting partner.")
    demand_score = serializers.IntegerField(
        allow_null=True, help_text="Admin-entered 0-100 signal. Null when unscored."
    )
    estimated_monthly_earnings = serializers.CharField(
        allow_null=True, help_text="Admin-entered estimate as a decimal string."
    )
    submitted_at = serializers.DateTimeField()


class MieRecommendationsSerializer(serializers.Serializer):
    """The MIE Recommendation screen."""

    pending_total = serializers.IntegerField(
        help_text="Every idea still awaiting a decision."
    )
    scored_total = serializers.IntegerField(
        help_text="How many of those carry a demand score, so coverage is visible."
    )
    results = MieRecommendationRowSerializer(many=True)
