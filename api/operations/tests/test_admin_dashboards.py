"""The three admin dashboards backed by the operations domain.

The recurring assertion across these: a metric with nothing behind it
must be **null**, not zero. On a health or spend dashboard those two mean
very different things, and defaulting to zero is how an unmonitored
service comes to look perfectly healthy.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.tests.factories import make_draft_course, make_user
from api.operations.enums import (
    CostCategory,
    EnrollmentStatus,
    PipelineJobStatus,
    PipelineStage,
    ProviderKind,
    ServiceStatus,
)
from api.operations.tests.factories import (
    make_enrollment,
    make_health_sample,
    make_pipeline_job,
    make_production_cost,
    make_provider,
    make_service,
)
from api.users.enums import UserRole

HEALTH = "/api/v1/admin/system-health/"
PIPELINE = "/api/v1/admin/pipeline/"
ANALYTICS = "/api/v1/admin/analytics/"


class AdminDashboardAccessTests(APITestCase):
    def test_all_three_require_admin(self):
        creator = make_user(role=UserRole.COURSE_CREATOR)

        for url in (HEALTH, PIPELINE, ANALYTICS):
            with self.subTest(url=url):
                self.client.force_authenticate(None)
                self.assertEqual(
                    self.client.get(url).status_code, status.HTTP_401_UNAUTHORIZED
                )
                self.client.force_authenticate(creator)
                self.assertEqual(
                    self.client.get(url).status_code, status.HTTP_403_FORBIDDEN
                )


class SystemHealthTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

    def _row(self, name, query=""):
        """One service row by name - the registry is seeded, so position
        is not a stable way to find the service under test."""

        rows = {
            row["name"]: row
            for row in self.client.get(f"{HEALTH}{query}").data["services"]
        }
        return rows[name]

    def test_unsampled_service_reports_null_uptime_not_perfect(self):
        """The bug this guards: defaulting to 100% would make an
        unmonitored service look healthiest of all."""

        make_service(name="Never Probed")

        row = self._row("Never Probed")

        self.assertIsNone(row["uptime_percent"])
        self.assertIsNone(row["status"])
        self.assertEqual(row["sample_count"], 0)

    def test_uptime_is_the_share_of_operational_samples(self):
        service = make_service(name="Gateway")
        for _ in range(3):
            make_health_sample(service=service, status=ServiceStatus.OPERATIONAL)
        make_health_sample(service=service, status=ServiceStatus.DOWN)

        row = self._row("Gateway")

        self.assertEqual(row["uptime_percent"], 75.0)
        self.assertEqual(row["sample_count"], 4)

    def test_status_reflects_the_most_recent_sample(self):
        service = make_service(name="Gateway")
        make_health_sample(
            service=service,
            status=ServiceStatus.OPERATIONAL,
            checked_at=timezone.now() - timedelta(hours=2),
        )
        make_health_sample(service=service, status=ServiceStatus.DEGRADED)

        data = self.client.get(HEALTH).data

        self.assertEqual(self._row("Gateway")["status"], ServiceStatus.DEGRADED)
        self.assertEqual(data["degraded_count"], 1)

    def test_samples_outside_the_window_are_ignored(self):
        service = make_service(name="Gateway")
        make_health_sample(
            service=service,
            status=ServiceStatus.DOWN,
            checked_at=timezone.now() - timedelta(days=90),
        )

        row = self._row("Gateway", query="?window_days=30")

        self.assertIsNone(row["uptime_percent"])

    def test_inactive_services_are_hidden(self):
        """Asserted by absence: the registry ships pre-seeded, so an empty
        list is not the right expectation."""

        make_service(name="Retired", is_active=False)

        names = [row["name"] for row in self.client.get(HEALTH).data["services"]]

        self.assertNotIn("Retired", names)

    def test_latency_averages_only_measured_probes(self):
        service = make_service(name="Gateway")
        make_health_sample(service=service, latency_ms=100)
        make_health_sample(service=service, latency_ms=200)
        make_health_sample(service=service, latency_ms=None, status=ServiceStatus.DOWN)

        row = self._row("Gateway")

        self.assertEqual(row["avg_latency_ms"], 150)

    def test_absurd_window_is_clamped_rather_than_erroring(self):
        for value in ("0", "-5", "99999", "abc"):
            with self.subTest(value=value):
                response = self.client.get(f"{HEALTH}?window_days={value}")
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertGreaterEqual(response.data["window_days"], 1)
                self.assertLessEqual(response.data["window_days"], 365)


class PipelineOverviewTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))
        self.course = make_draft_course()

    def test_every_stage_is_present_even_when_empty(self):
        data = self.client.get(PIPELINE).data

        self.assertEqual(len(data["stages"]), len(PipelineStage.choices))
        self.assertTrue(all(row["total"] == 0 for row in data["stages"]))

    def test_stages_are_in_funnel_order(self):
        stages = [row["stage"] for row in self.client.get(PIPELINE).data["stages"]]

        self.assertEqual(stages, [choice.value for choice in PipelineStage])

    def test_tiles_count_by_status(self):
        make_pipeline_job(course=self.course, status=PipelineJobStatus.QUEUED)
        make_pipeline_job(course=self.course, status=PipelineJobStatus.RUNNING)
        make_pipeline_job(course=self.course, status=PipelineJobStatus.FAILED)
        make_pipeline_job(
            course=self.course,
            status=PipelineJobStatus.COMPLETED,
            finished_at=timezone.now(),
        )

        data = self.client.get(PIPELINE).data

        self.assertEqual(data["active_jobs"], 2)  # queued + running
        self.assertEqual(data["queue_depth"], 1)
        self.assertEqual(data["failed_or_retrying"], 1)
        self.assertEqual(data["completed_today"], 1)

    def test_completed_yesterday_is_not_completed_today(self):
        make_pipeline_job(
            course=self.course,
            status=PipelineJobStatus.COMPLETED,
            finished_at=timezone.now() - timedelta(days=1),
        )

        self.assertEqual(self.client.get(PIPELINE).data["completed_today"], 0)

    def test_avg_pipeline_seconds_is_null_without_completions(self):
        make_pipeline_job(course=self.course, status=PipelineJobStatus.QUEUED)

        self.assertIsNone(self.client.get(PIPELINE).data["avg_pipeline_seconds"])

    def test_avg_pipeline_seconds_measures_completed_jobs(self):
        started = timezone.now() - timedelta(minutes=10)
        make_pipeline_job(
            course=self.course,
            status=PipelineJobStatus.COMPLETED,
            started_at=started,
            finished_at=started + timedelta(minutes=10),
        )

        self.assertEqual(self.client.get(PIPELINE).data["avg_pipeline_seconds"], 600)

    def test_provider_readings_carry_their_staleness(self):
        # WellSaid Labs is seeded by migration; update it rather than
        # creating a second one against the unique name constraint.
        from api.operations.models import Provider

        Provider.objects.filter(name="WellSaid Labs").update(
            current_load_percent=89,
            current_queue_depth=12,
            readings_updated_at=timezone.now(),
        )
        make_provider(name="Never Polled", kind=ProviderKind.VIDEO)

        providers = {p["name"]: p for p in self.client.get(PIPELINE).data["providers"]}

        self.assertEqual(providers["WellSaid Labs"]["load_percent"], 89)
        self.assertIsNotNone(providers["WellSaid Labs"]["readings_updated_at"])
        # Never polled must read as unknown, not as an idle provider.
        self.assertIsNone(providers["Never Polled"]["load_percent"])
        self.assertIsNone(providers["Never Polled"]["readings_updated_at"])


class AdminAnalyticsTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

    def test_empty_platform_reports_nulls_not_zeroes(self):
        data = self.client.get(ANALYTICS).data

        self.assertIsNone(data["enrollment"]["avg_completion_rate"])
        self.assertIsNone(data["cost"]["overall_cost"])
        self.assertIsNone(data["cost"]["cost_per_course"])
        self.assertIsNone(data["kpis"]["first_pass_approval_percent"])
        # Counts genuinely are zero, and say so.
        self.assertEqual(data["catalog"]["total_catalog"], 0)
        self.assertEqual(data["enrollment"]["total_enrollment"], 0)

    def test_completion_rate_averages_progress(self):
        course = make_draft_course()
        make_enrollment(course=course, progress_percent=100,
                        status=EnrollmentStatus.COMPLETED)
        make_enrollment(course=course, progress_percent=50)

        data = self.client.get(ANALYTICS).data["enrollment"]

        self.assertEqual(data["total_enrollment"], 2)
        self.assertEqual(data["completed"], 1)
        self.assertEqual(data["avg_completion_rate"], 75.0)

    def test_cost_is_returned_as_decimal_strings(self):
        """Money must not round-trip through float."""

        make_production_cost(amount="0.0001", category=CostCategory.VOICE)
        make_production_cost(amount="1.2345", category=CostCategory.VIDEO)

        cost = self.client.get(ANALYTICS).data["cost"]

        self.assertIsInstance(cost["overall_cost"], str)
        self.assertEqual(Decimal(cost["overall_cost"]), Decimal("1.2346"))

    def test_cost_splits_by_category(self):
        make_production_cost(amount="5.0000", category=CostCategory.VOICE)
        make_production_cost(amount="3.0000", category=CostCategory.VIDEO)

        by_category = {
            row["category"]: Decimal(row["amount"])
            for row in self.client.get(ANALYTICS).data["cost"]["by_category"]
        }

        self.assertEqual(by_category[CostCategory.VOICE], Decimal("5.0000"))
        self.assertEqual(by_category[CostCategory.VIDEO], Decimal("3.0000"))

    def test_cost_per_course_divides_by_published_courses(self):
        published = make_draft_course()
        published.status = CourseStatus.PUBLISHED
        published.save(update_fields=["status"])
        make_production_cost(amount="10.0000")

        cost = self.client.get(ANALYTICS).data["cost"]

        self.assertEqual(Decimal(cost["cost_per_course"]), Decimal("10.00"))

    def test_distribution_lists_every_channel_including_zeroes(self):
        data = self.client.get(ANALYTICS).data["distribution"]

        self.assertEqual({row["channel"] for row in data},
                         {"SOLUDESK", "UDEMY", "COURSERA"})
        self.assertTrue(all(row["count"] == 0 for row in data))

    def test_costs_outside_the_period_are_excluded(self):
        make_production_cost(
            amount="99.0000", incurred_at=timezone.now() - timedelta(days=200)
        )

        cost = self.client.get(f"{ANALYTICS}?period=7d").data["cost"]

        self.assertIsNone(cost["cost_in_period"])
        # Lifetime total still sees it.
        self.assertEqual(Decimal(cost["overall_cost"]), Decimal("99.0000"))

    def test_unknown_period_falls_back_to_the_default(self):
        data = self.client.get(f"{ANALYTICS}?period=nonsense").data

        self.assertEqual(data["period"], "7d")


class AdminOverviewTilesTests(APITestCase):
    """The dashboard's headline tiles and the two trend charts."""

    URL = "/api/v1/admin/overview/"

    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

    def test_legacy_blocks_survive_alongside_the_new_ones(self):
        data = self.client.get(self.URL).data

        for key in ("users", "courses", "kyc", "withdrawals", "wallet_totals"):
            self.assertIn(key, data)
        for key in ("today", "production_trend", "cost_trend"):
            self.assertIn(key, data)

    def test_change_percent_is_null_without_a_baseline(self):
        """Zero would claim flat performance where nothing is comparable."""

        tiles = self.client.get(self.URL).data["today"]

        self.assertIsNone(tiles["courses_created_change_percent"])
        self.assertIsNone(tiles["daily_cost_change_percent"])
        self.assertIsNone(tiles["daily_cost"])
        self.assertIsNone(tiles["avg_cost_per_course"])

    def test_courses_created_today_counts_only_today(self):
        make_draft_course()
        old = make_draft_course()
        type(old).objects.filter(pk=old.pk).update(
            created_datetime=timezone.now() - timedelta(days=3)
        )

        tiles = self.client.get(self.URL).data["today"]

        self.assertEqual(tiles["courses_created_today"], 1)

    def test_daily_cost_sums_todays_spend_only(self):
        make_production_cost(amount="4.0000")
        make_production_cost(
            amount="99.0000", incurred_at=timezone.now() - timedelta(days=2)
        )

        tiles = self.client.get(self.URL).data["today"]

        self.assertEqual(Decimal(tiles["daily_cost"]), Decimal("4.0000"))

    def test_avg_cost_per_course_needs_both_spend_and_published(self):
        make_production_cost(amount="20.0000")
        published = make_draft_course()
        published.status = CourseStatus.PUBLISHED
        published.save(update_fields=["status"])

        tiles = self.client.get(self.URL).data["today"]

        self.assertEqual(Decimal(tiles["avg_cost_per_course"]), Decimal("20.00"))

    def test_trends_are_zero_filled_for_a_stable_axis(self):
        data = self.client.get(self.URL).data

        self.assertEqual(len(data["production_trend"]), 7)
        self.assertEqual(len(data["cost_trend"]), 7)
        dates = [row["date"] for row in data["production_trend"]]
        self.assertEqual(dates, sorted(dates))
        self.assertTrue(all(row["count"] == 0 for row in data["production_trend"]))


class MieRecommendationsTests(APITestCase):
    """Read-only ranking over the MIE queue. Must not alter MIE itself."""

    URL = "/api/v1/admin/mie-recommendations/"

    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

    def _submission(self, *, title, score=None, earnings=None, decided=False):
        from api.mie.enums import SubmissionStatus
        from api.mie.tests.factories import make_approved_developer, make_submission

        developer, _key = make_approved_developer()
        submission = make_submission(developer=developer, title=title)
        if score is not None:
            submission.demand_score = score
        if earnings is not None:
            submission.estimated_monthly_earnings = earnings
        if decided:
            submission.status = SubmissionStatus.APPROVED
            submission.decided_at = timezone.now()
        submission.save()
        return submission

    def test_requires_admin(self):
        self.client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))

        self.assertEqual(
            self.client.get(self.URL).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_ranks_by_demand_score(self):
        self._submission(title="Low demand", score=10)
        self._submission(title="High demand", score=90)
        self._submission(title="Mid demand", score=50)

        titles = [row["title"] for row in self.client.get(self.URL).data["results"]]

        self.assertEqual(titles, ["High demand", "Mid demand", "Low demand"])

    def test_unscored_ideas_sort_last_but_are_not_hidden(self):
        self._submission(title="Unscored")
        self._submission(title="Scored", score=40)

        data = self.client.get(self.URL).data

        self.assertEqual(data["results"][0]["title"], "Scored")
        self.assertEqual(data["results"][-1]["title"], "Unscored")
        self.assertEqual(data["pending_total"], 2)
        self.assertEqual(data["scored_total"], 1)

    def test_decided_ideas_are_excluded(self):
        self._submission(title="Already approved", score=99, decided=True)
        self._submission(title="Still pending", score=1)

        data = self.client.get(self.URL).data

        titles = [row["title"] for row in data["results"]]
        self.assertEqual(titles, ["Still pending"])
        self.assertEqual(data["pending_total"], 1)

    def test_earnings_are_decimal_strings(self):
        self._submission(title="Lucrative", score=80, earnings="4200.00")

        row = self.client.get(self.URL).data["results"][0]

        self.assertIsInstance(row["estimated_monthly_earnings"], str)
        self.assertEqual(Decimal(row["estimated_monthly_earnings"]), Decimal("4200.00"))

    def test_limit_is_capped_and_survives_garbage(self):
        for index in range(3):
            self._submission(title=f"Idea {index}", score=index)

        self.assertEqual(len(self.client.get(f"{self.URL}?limit=1").data["results"]), 1)
        self.assertEqual(
            len(self.client.get(f"{self.URL}?limit=abc").data["results"]), 3
        )
        self.assertEqual(
            len(self.client.get(f"{self.URL}?limit=99999").data["results"]), 3
        )
