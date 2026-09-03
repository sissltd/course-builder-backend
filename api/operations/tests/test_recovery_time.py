"""Time-to-recovery: how long a service took to go green after failing."""

from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from api.courses.tests.factories import make_user
from api.operations.enums import ServiceStatus
from api.operations.tests.factories import make_health_sample, make_service
from api.users.enums import UserRole

HEALTH = "/api/v1/admin/system-health/"


class RecoveryTimeTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))
        self.service = make_service(name="Gateway")
        self.base = timezone.now() - timedelta(hours=6)

    def _sample(self, status, minutes):
        make_health_sample(
            service=self.service,
            status=status,
            latency_ms=None if status == ServiceStatus.DOWN else 100,
            checked_at=self.base + timedelta(minutes=minutes),
        )

    def _row(self):
        return {
            row["name"]: row for row in self.client.get(HEALTH).data["services"]
        }["Gateway"]

    def test_no_failure_means_no_recovery_time(self):
        self._sample(ServiceStatus.OPERATIONAL, 0)

        self.assertIsNone(self._row()["last_recovery_seconds"])

    def test_measures_from_failure_to_green(self):
        self._sample(ServiceStatus.OPERATIONAL, 0)
        self._sample(ServiceStatus.DOWN, 10)
        self._sample(ServiceStatus.OPERATIONAL, 70)

        self.assertEqual(self._row()["last_recovery_seconds"], 60 * 60)

    def test_measures_from_the_first_failure_of_a_run_not_the_last(self):
        """A service that flaps for an hour took an hour to recover, not
        the few minutes of its final blip."""

        self._sample(ServiceStatus.OPERATIONAL, 0)
        self._sample(ServiceStatus.DOWN, 10)
        self._sample(ServiceStatus.DEGRADED, 40)
        self._sample(ServiceStatus.DOWN, 60)
        self._sample(ServiceStatus.OPERATIONAL, 70)

        self.assertEqual(self._row()["last_recovery_seconds"], 60 * 60)

    def test_a_service_still_down_has_not_recovered(self):
        self._sample(ServiceStatus.OPERATIONAL, 0)
        self._sample(ServiceStatus.DOWN, 10)

        self.assertIsNone(self._row()["last_recovery_seconds"])

    def test_latest_recovery_wins_over_an_older_one(self):
        self._sample(ServiceStatus.OPERATIONAL, 0)
        self._sample(ServiceStatus.DOWN, 10)
        self._sample(ServiceStatus.OPERATIONAL, 130)   # 2h recovery
        self._sample(ServiceStatus.DOWN, 200)
        self._sample(ServiceStatus.OPERATIONAL, 230)   # 30m recovery

        self.assertEqual(self._row()["last_recovery_seconds"], 30 * 60)

    def test_degraded_counts_as_a_failure_to_recover_from(self):
        self._sample(ServiceStatus.OPERATIONAL, 0)
        self._sample(ServiceStatus.DEGRADED, 10)
        self._sample(ServiceStatus.OPERATIONAL, 25)

        self.assertEqual(self._row()["last_recovery_seconds"], 15 * 60)

    def test_overall_average_ignores_services_that_never_recovered(self):
        other = make_service(name="Stuck")
        make_health_sample(
            service=other,
            status=ServiceStatus.DOWN,
            checked_at=self.base + timedelta(minutes=5),
        )
        self._sample(ServiceStatus.OPERATIONAL, 0)
        self._sample(ServiceStatus.DOWN, 10)
        self._sample(ServiceStatus.OPERATIONAL, 70)

        data = self.client.get(HEALTH).data

        # Only Gateway's 1h recovery counts; Stuck is still down.
        self.assertEqual(data["avg_recovery_seconds"], 60 * 60)

    def test_average_is_null_when_nothing_has_recovered(self):
        self._sample(ServiceStatus.DOWN, 10)

        self.assertIsNone(self.client.get(HEALTH).data["avg_recovery_seconds"])
