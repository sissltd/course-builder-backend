"""The two guardrails that bind platform-owned (SYSTEM) accounts, and the
command that creates one.

Every test here also has to prove the negative: an EXTERNAL developer must
be able to do the same thing at the same volume and never meet either
guardrail. That is the whole contract - the crawler submits through the
public API like anyone else, and only its own account is fenced in.

Thresholds are overridden per test rather than exercised at their real
defaults (20 submissions, 10 decisions): the point is the boundary, and
driving 20 HTTP calls to find it would also collide with the per-minute
ingest throttle.
"""

from io import StringIO

from django.core import mail
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.tests.factories import make_user
from api.mie.enums import (
    DeveloperAccountStatus,
    MiePlanType,
    MieSourceType,
    SubmissionStatus,
)
from api.mie.models import CourseSubmission, DeveloperAccount
from api.mie.tests.factories import (
    make_approved_developer,
    make_rejection_reason,
    make_submission,
    make_system_developer,
)
from api.notification.models import Notification
from api.users.enums import (
    UserActivityActionEnums,
    UserActivityCategoryEnums,
    UserRole,
)
from api.users.models import UserActivityLog

INGEST_URL = "/api/v1/mie/v1/submissions/"
QUEUE_URL = "/api/v1/mie/admin/submissions/"


@override_settings(MIE_SYSTEM_DAILY_SUBMISSION_CAP=3)
class DailySubmissionCapTests(APITestCase):
    """The rolling 24-hour cap on a SYSTEM account's ingestion."""

    def setUp(self):
        # The per-minute ingest throttle is cached per IP and would leak
        # from one test method into the next; these classes submit in
        # bursts.
        cache.clear()
        self.crawler, self.crawler_key = make_system_developer()
        self.partner, self.partner_key = make_approved_developer()

    def _submit(self, key, title):
        return self.client.post(
            INGEST_URL, {"title": title}, format="json", HTTP_X_MIE_API_KEY=key
        )

    def test_system_account_is_refused_once_the_cap_is_used_up(self):
        for index in range(3):
            self.assertEqual(
                self._submit(self.crawler_key, f"Crawler Idea {index}").status_code,
                status.HTTP_201_CREATED,
            )

        refused = self._submit(self.crawler_key, "Crawler Idea Over The Line")

        self.assertEqual(refused.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(refused.data["errors"][0]["code"], "throttled")
        # Retry-After is the whole point of raising Throttled rather than a
        # bare 400: the crawler is told when to come back, not just "no".
        self.assertGreater(int(refused.headers["Retry-After"]), 0)
        # Nothing was stored for the refused call - the cap refuses before
        # the row exists, so a capped crawler cannot fill the queue.
        self.assertEqual(CourseSubmission.objects.filter(developer=self.crawler).count(), 3)

    def test_external_account_at_the_same_volume_is_never_capped(self):
        for index in range(4):
            with self.subTest(index=index):
                self.assertEqual(
                    self._submit(self.partner_key, f"Partner Idea {index}").status_code,
                    status.HTTP_201_CREATED,
                )

        self.assertEqual(CourseSubmission.objects.filter(developer=self.partner).count(), 4)

    def test_dedup_short_circuits_spend_the_allowance_too(self):
        """A crawler stuck resubmitting one title is exactly what the cap
        exists to stop, so every outcome counts - not just the queued ones."""

        make_submission(title="Already Waiting In The Queue")

        first = self._submit(self.crawler_key, "Fresh Crawler Idea")
        duplicate = self._submit(self.crawler_key, "already waiting in the queue")
        third = self._submit(self.crawler_key, "Another Fresh Crawler Idea")
        refused = self._submit(self.crawler_key, "One Idea Too Many")

        self.assertEqual(first.data["status"], SubmissionStatus.PENDING_REVIEW)
        self.assertEqual(duplicate.data["status"], SubmissionStatus.DUPLICATE_IN_QUEUE)
        self.assertEqual(third.status_code, status.HTTP_201_CREATED)
        self.assertEqual(refused.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


@override_settings(
    MIE_BREAKER_MIN_DECISIONS=3,
    MIE_BREAKER_REJECTION_RATE=0.8,
    MIE_BREAKER_WINDOW_DAYS=7,
)
class RejectionCircuitBreakerTests(APITestCase):
    """Suspension of a SYSTEM account whose ideas admins keep rejecting."""

    def setUp(self):
        cache.clear()
        self.superadmin = make_user(role=UserRole.SUPER_ADMIN)
        self.client.force_authenticate(self.superadmin)
        self.reason = make_rejection_reason(label="Prohibited subject")
        self.crawler, self.crawler_key = make_system_developer()

    def _reject(self, submission):
        return self.client.post(
            f"{QUEUE_URL}{submission.id}/reject/",
            {"rejection_reason": "Prohibited subject"},
            format="json",
        )

    def _approve(self, submission):
        return self.client.post(f"{QUEUE_URL}{submission.id}/approve/", {}, format="json")

    def _reject_many(self, account, count):
        for index in range(count):
            self._reject(make_submission(developer=account, title=f"Idea {account.id} {index}"))

    def test_breaker_suspends_the_account_at_the_threshold(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._reject_many(self.crawler, 3)

        self.crawler.refresh_from_db()
        self.assertEqual(self.crawler.status, DeveloperAccountStatus.SUSPENDED)
        # Suspension, not rejection: key material and queue history survive,
        # so approving the account again is the whole recovery path.
        self.assertTrue(self.crawler.api_key_hash)
        self.assertEqual(CourseSubmission.objects.filter(developer=self.crawler).count(), 3)

    def test_breaker_holds_below_the_minimum_decision_count(self):
        """Two rejections read as a 100% failure rate; the minimum is what
        stops a brand-new crawler being suspended by its first bad day."""

        with self.captureOnCommitCallbacks(execute=True):
            self._reject_many(self.crawler, 2)

        self.crawler.refresh_from_db()
        self.assertEqual(self.crawler.status, DeveloperAccountStatus.APPROVED)

    def test_breaker_never_touches_an_external_account(self):
        partner, _key = make_approved_developer()

        with self.captureOnCommitCallbacks(execute=True):
            self._reject_many(partner, 4)

        partner.refresh_from_db()
        self.assertEqual(partner.status, DeveloperAccountStatus.APPROVED)

    def test_approvals_neither_trip_the_breaker_nor_count_as_failures(self):
        """Four approvals and one rejection is a 20% rejection rate - well
        under the threshold - even though the decision count is past the
        minimum."""

        with self.captureOnCommitCallbacks(execute=True):
            for index in range(4):
                self._approve(make_submission(developer=self.crawler, title=f"Good {index}"))
            self._reject(make_submission(developer=self.crawler, title="Bad one"))

        self.crawler.refresh_from_db()
        self.assertEqual(self.crawler.status, DeveloperAccountStatus.APPROVED)

    def test_trip_is_audited_and_alerts_the_superadmin(self):
        """A suspension nobody is told about is a crawler that has silently
        stopped. The audit row carries the arithmetic behind the decision so
        the alert can be judged rather than just believed.

        One recipient, because a DB constraint permits exactly one
        SUPER_ADMIN row; the service fans out to every match.
        """

        with self.captureOnCommitCallbacks(execute=True):
            self._reject_many(self.crawler, 3)

        audit = UserActivityLog.objects.get(
            category=UserActivityCategoryEnums.ALERT,
            action=UserActivityActionEnums.ACCOUNT_SUSPENDED,
        )
        self.assertEqual(audit.details["developer_email"], self.crawler.email)
        self.assertEqual(audit.details["decided"], 3)
        self.assertEqual(audit.details["rejected"], 3)
        self.assertEqual(
            list(
                Notification.objects.filter(
                    title__icontains="circuit breaker"
                ).values_list("receiver_id", flat=True)
            ),
            [self.superadmin.id],
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.superadmin.email])
        self.assertIn(self.crawler.email, mail.outbox[0].body)

    def test_a_suspended_crawler_cannot_ingest_but_keeps_its_key(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._reject_many(self.crawler, 3)

        # Drop the admin session: this call has to arrive as the crawler,
        # over its API key, or it never reaches the key checks at all.
        self.client.force_authenticate(user=None)
        refused = self.client.post(
            INGEST_URL,
            {"title": "Post-suspension idea"},
            format="json",
            HTTP_X_MIE_API_KEY=self.crawler_key,
        )

        self.assertEqual(refused.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(refused.data["errors"][0]["code"], "account_suspended")


class ProvisionSystemAccountCommandTests(APITestCase):
    """`manage.py provision_mie_system_account` - the only path to a SYSTEM
    account, and the only place the crawler's raw key is ever shown."""

    def setUp(self):
        self.superadmin = make_user(role=UserRole.SUPER_ADMIN)

    def _run(self, email="crawler@soludesks.com", actor_email=None):
        out = StringIO()
        call_command(
            "provision_mie_system_account",
            "--email",
            email,
            "--webhook-url",
            "https://crawler.soludesks.com/mie/webhooks/",
            "--actor-email",
            actor_email or self.superadmin.email,
            stdout=out,
        )
        return out.getvalue()

    def test_fresh_run_creates_an_approved_system_account_and_prints_the_key(self):
        output = self._run()

        account = DeveloperAccount.objects.get(email="crawler@soludesks.com")
        self.assertEqual(account.source_type, MieSourceType.SYSTEM)
        self.assertEqual(account.status, DeveloperAccountStatus.APPROVED)
        # The platform never pays itself for its own ideas.
        self.assertEqual(account.plan_type, MiePlanType.BYPASS_ACCOUNT)
        self.assertTrue(account.api_key_hash)
        self.assertIn("scb_live_", output)
        self.assertTrue(
            UserActivityLog.objects.filter(
                category=UserActivityCategoryEnums.CONFIGURATION,
                action=UserActivityActionEnums.ACCOUNT_CREATED,
            ).exists()
        )

    def test_rerunning_changes_nothing_and_issues_no_second_key(self):
        """Re-running must be safe: a second key would silently invalidate
        the one the crawler is already using."""

        self._run()
        original = DeveloperAccount.objects.get(email="crawler@soludesks.com")

        output = self._run()

        original.refresh_from_db()
        self.assertEqual(DeveloperAccount.objects.filter(source_type=MieSourceType.SYSTEM).count(), 1)
        self.assertEqual(
            original.api_key_hash,
            DeveloperAccount.objects.get(id=original.id).api_key_hash,
        )
        self.assertNotIn("scb_live_", output)
        self.assertIn("already a system account", output)

    def test_an_email_held_by_an_external_developer_is_refused(self):
        """Converting a third party would put them under crawler guardrails
        and on a no-payout plan without them ever asking for either."""

        partner, _key = make_approved_developer(email="partner@studio.io")

        with self.assertRaises(CommandError) as ctx:
            self._run(email="partner@studio.io")

        self.assertIn("external developer account", str(ctx.exception))
        partner.refresh_from_db()
        self.assertEqual(partner.source_type, MieSourceType.EXTERNAL)

    def test_an_actor_below_super_admin_is_refused(self):
        admin = make_user(role=UserRole.ADMIN)

        with self.assertRaises(CommandError) as ctx:
            self._run(actor_email=admin.email)

        self.assertIn("permission", str(ctx.exception).lower())
        self.assertFalse(DeveloperAccount.objects.exists())

    def test_an_unknown_actor_email_is_refused(self):
        with self.assertRaises(CommandError) as ctx:
            self._run(actor_email="nobody@example.com")

        self.assertIn("nobody@example.com", str(ctx.exception))
        self.assertFalse(DeveloperAccount.objects.exists())
