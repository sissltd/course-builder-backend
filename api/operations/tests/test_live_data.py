"""The paths that make System Health and APE Pipeline populate themselves.

Registry rows are seeded by migration; the probe writes health samples on
a schedule; the pipeline write-path records jobs and provider readings.
Together they mean the screens fill in as real activity arrives rather
than needing someone to backfill them by hand.
"""

from unittest.mock import patch

from rest_framework.test import APITestCase

from api.courses.tests.factories import make_draft_course, make_user
from api.operations.enums import (
    PipelineJobStatus,
    PipelineStage,
    ServiceStatus,
)
from api.operations.models import (
    Provider,
    Service,
    ServiceHealthSample,
)
from api.operations.services import pipeline_service, probe_service
from api.users.enums import UserRole

HEALTH = "/api/v1/admin/system-health/"
PIPELINE = "/api/v1/admin/pipeline/"


class SeededRegistryTests(APITestCase):
    """The migration seeds exactly the services and providers the design
    names, so the screens have their rows on first deploy."""

    def test_every_designed_service_is_registered(self):
        expected = {
            "Creator Studio",
            "API Gateway",
            "APE Pipeline",
            "MIE Crawler",
            "PostgreSQL",
            "Redis Cache",
            "S3/CDN",
            "WellSaid TTS",
            "Colossyan Video",
            "Intron Sahara",
        }

        self.assertTrue(
            expected.issubset(set(Service.objects.values_list("name", flat=True)))
        )

    def test_every_designed_provider_is_registered(self):
        expected = {
            "WellSaid Labs",
            "Murf AI",
            "Google TTS",
            "Colossyan",
            "Synthesia",
            "HeyGen",
        }

        self.assertTrue(
            expected.issubset(set(Provider.objects.values_list("name", flat=True)))
        )

    def test_services_appear_on_the_dashboard_before_any_probe(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

        rows = self.client.get(HEALTH).data["services"]

        self.assertGreaterEqual(len(rows), 10)
        # Present but honestly unmeasured until the probe runs.
        by_name = {row["name"]: row for row in rows}
        self.assertIsNone(by_name["Creator Studio"]["uptime_percent"])


class HealthProbeTests(APITestCase):
    def test_probe_records_a_sample_per_probeable_service(self):
        before = ServiceHealthSample.objects.count()

        report = probe_service.run_probes()

        self.assertGreater(ServiceHealthSample.objects.count(), before)
        self.assertEqual(
            sum(report.values()) - report["skipped"],
            ServiceHealthSample.objects.count() - before,
        )

    def test_database_probe_reports_operational(self):
        probe_service.run_probes()

        sample = ServiceHealthSample.objects.filter(
            service__name="PostgreSQL"
        ).latest("checked_at")

        self.assertEqual(sample.status, ServiceStatus.OPERATIONAL)
        self.assertIsNotNone(sample.latency_ms)

    def test_a_failing_probe_is_recorded_as_down_not_raised(self):
        """One broken dependency must not stop the others being measured."""

        with patch.dict(
            probe_service.PROBES,
            {"PostgreSQL": lambda: (_ for _ in ()).throw(RuntimeError("boom"))},
        ):
            report = probe_service.run_probes()

        sample = ServiceHealthSample.objects.filter(
            service__name="PostgreSQL"
        ).latest("checked_at")
        self.assertEqual(sample.status, ServiceStatus.DOWN)
        self.assertIsNone(sample.latency_ms)
        self.assertGreaterEqual(report["down"], 1)
        # Others were still measured.
        self.assertTrue(
            ServiceHealthSample.objects.filter(service__name="Redis Cache").exists()
        )

    def test_a_slow_probe_is_degraded_not_operational(self):
        with patch.dict(
            probe_service.PROBES,
            {"PostgreSQL": lambda: probe_service.DEGRADED_LATENCY_MS + 1},
        ):
            probe_service.run_probes()

        sample = ServiceHealthSample.objects.filter(
            service__name="PostgreSQL"
        ).latest("checked_at")
        self.assertEqual(sample.status, ServiceStatus.DEGRADED)

    def test_probing_makes_the_dashboard_show_real_uptime(self):
        """End to end: probe, then read the screen."""

        probe_service.run_probes()
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

        rows = {row["name"]: row for row in self.client.get(HEALTH).data["services"]}

        self.assertIsNotNone(rows["PostgreSQL"]["uptime_percent"])
        self.assertEqual(rows["PostgreSQL"]["sample_count"], 1)

    def test_management_command_runs(self):
        from django.core.management import call_command

        call_command("probe_service_health")

        self.assertTrue(ServiceHealthSample.objects.exists())


class PipelineWritePathTests(APITestCase):
    def setUp(self):
        self.course = make_draft_course()
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

    def test_recording_a_job_moves_the_funnel(self):
        pipeline_service.record_job(
            course=self.course, stage=PipelineStage.CURRICULUM
        )

        stages = {
            row["stage"]: row for row in self.client.get(PIPELINE).data["stages"]
        }

        self.assertEqual(stages[PipelineStage.CURRICULUM]["total"], 1)
        self.assertEqual(stages[PipelineStage.CURRICULUM]["active"], 1)

    def test_completing_a_job_fills_completed_today_and_duration(self):
        job = pipeline_service.record_job(
            course=self.course, stage=PipelineStage.AUTO_QA
        )
        pipeline_service.start_job(job=job)
        pipeline_service.complete_job(job=job)

        data = self.client.get(PIPELINE).data

        self.assertEqual(data["completed_today"], 1)
        self.assertIsNotNone(data["avg_pipeline_seconds"])

    def test_retrying_and_failing_are_distinguished(self):
        retrying = pipeline_service.record_job(
            course=self.course, stage=PipelineStage.MEDIA_PRODUCTION
        )
        terminal = pipeline_service.record_job(
            course=self.course, stage=PipelineStage.MEDIA_PRODUCTION
        )
        pipeline_service.fail_job(job=retrying, error="rate limited", will_retry=True)
        pipeline_service.fail_job(job=terminal, error="unsupported codec")

        retrying.refresh_from_db()
        terminal.refresh_from_db()

        self.assertEqual(retrying.status, PipelineJobStatus.RETRYING)
        self.assertIsNone(retrying.finished_at)
        self.assertEqual(terminal.status, PipelineJobStatus.FAILED)
        self.assertIsNotNone(terminal.finished_at)
        self.assertEqual(self.client.get(PIPELINE).data["failed_or_retrying"], 2)

    def test_start_counts_an_attempt(self):
        job = pipeline_service.record_job(
            course=self.course, stage=PipelineStage.TOPIC_INTAKE
        )

        pipeline_service.start_job(job=job)
        pipeline_service.start_job(job=job)

        job.refresh_from_db()
        self.assertEqual(job.attempts, 2)

    def test_provider_readings_stamp_their_own_freshness(self):
        provider = Provider.objects.get(name="WellSaid Labs")

        pipeline_service.update_provider_readings(
            provider=provider, load_percent=89, queue_depth=12
        )

        rows = {p["name"]: p for p in self.client.get(PIPELINE).data["providers"]}
        self.assertEqual(rows["WellSaid Labs"]["load_percent"], 89)
        self.assertEqual(rows["WellSaid Labs"]["queue_depth"], 12)
        self.assertIsNotNone(rows["WellSaid Labs"]["readings_updated_at"])
        # A provider never polled stays honestly unknown.
        self.assertIsNone(rows["HeyGen"]["load_percent"])
        self.assertIsNone(rows["HeyGen"]["readings_updated_at"])


class AnalyticsDesignFieldTests(APITestCase):
    """Fields the design's Analytics screen shows that were missing."""

    URL = "/api/v1/admin/analytics/"

    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

    def test_scorecard_carries_every_designed_kpi(self):
        kpis = self.client.get(self.URL).data["kpis"]

        for key in (
            "daily_output",
            "first_pass_approval_percent",
            "avg_pipeline_time_minutes",
            "cost_per_course",
            "review_turnaround_hours",
            "system_uptime_percent",
        ):
            self.assertIn(key, kpis)

    def test_targets_ship_with_the_figures(self):
        targets = self.client.get(self.URL).data["kpis"]["targets"]

        self.assertEqual(targets["daily_output"], "200+")
        self.assertEqual(targets["system_uptime_percent"], "99.9%")

    def test_total_earnings_is_present(self):
        data = self.client.get(self.URL).data

        self.assertIn("earnings", data)
        self.assertIn("total_earnings", data["earnings"])

    def test_uptime_kpi_follows_the_probe(self):
        probe_service.run_probes()

        kpis = self.client.get(self.URL).data["kpis"]

        self.assertIsNotNone(kpis["system_uptime_percent"])


class AdminOverviewPeriodTests(APITestCase):
    URL = "/api/v1/admin/overview/"

    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

    def test_period_controls_the_trend_length(self):
        for period, expected in (("24h", 1), ("7d", 7), ("31d", 31)):
            with self.subTest(period=period):
                data = self.client.get(f"{self.URL}?period={period}").data

                self.assertEqual(data["period"], period)
                self.assertEqual(len(data["production_trend"]), expected)
                self.assertEqual(len(data["cost_trend"]), expected)

    def test_unknown_period_falls_back_to_the_default(self):
        data = self.client.get(f"{self.URL}?period=nonsense").data

        self.assertEqual(data["period"], "7d")
