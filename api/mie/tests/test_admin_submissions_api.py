import json

from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.tests.factories import make_draft_course, make_user
from api.mie.enums import (
    SubmissionStatus,
    WebhookEventType,
)
from api.mie.models import WebhookEvent
from api.mie.services import submission_service
from api.mie.services.webhook_dispatcher import render_body
from api.mie.tests.factories import (
    make_approved_developer,
    make_decided_submission,
    make_rejection_reason,
    make_submission,
)
from api.users.enums import UserRole

QUEUE_URL = "/api/v1/mie/admin/submissions/"
REASONS_URL = "/api/v1/mie/admin/rejection-reasons/"


def _detail(pk):
    return f"{QUEUE_URL}{pk}/"


class AdminQueueAccessTests(APITestCase):
    def setUp(self):
        self.superadmin = make_user(role=UserRole.SUPER_ADMIN)

    def test_requires_authentication(self):
        response = self.client.get(QUEUE_URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_superadmin_forbidden(self):
        self.client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))
        response = self.client.get(QUEUE_URL)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class AdminQueueFilterTests(APITestCase):
    def setUp(self):
        self.superadmin = make_user(role=UserRole.SUPER_ADMIN)
        self.client.force_authenticate(self.superadmin)
        self.dev_a, _key_a = make_approved_developer(email="a@studio.io")
        self.dev_b, _key_b = make_approved_developer(email="b@studio.io")
        self.a_queued = make_submission(
            developer=self.dev_a, title="Rust Systems Course"
        )
        self.b_decided = make_decided_submission(
            developer=self.dev_b, title="Gardening Masterclass", approved=True
        )

    def _ids(self, query=""):
        response = self.client.get(f"{QUEUE_URL}{query}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [r["id"] for r in response.data["data"]["results"]]

    def test_unfiltered_lists_everything_newest_first(self):
        ids = self._ids()
        self.assertEqual(set(ids), {str(self.b_decided.id), str(self.a_queued.id)})
        self.assertEqual(ids[0], str(self.b_decided.id))  # decided later

    def test_filter_by_developer_id_and_email(self):
        self.assertEqual(self._ids("?developer=" + str(self.dev_a.id)), [str(self.a_queued.id)])
        self.assertEqual(self._ids("?email=b@studio.io"), [str(self.b_decided.id)])

    def test_filter_by_status_and_payout_flag(self):
        self.assertEqual(self._ids("?status=PENDING_REVIEW"), [str(self.a_queued.id)])
        self.assertEqual(self._ids("?status=APPROVED"), [str(self.b_decided.id)])
        self.assertEqual(self._ids("?payout_bypass=true"), [])

    def test_search_matches_title_or_email(self):
        self.assertIn(str(self.a_queued.id), self._ids("?search=rust"))
        self.assertIn(str(self.b_decided.id), self._ids("?search=b@studio"))

    def test_date_range_filters(self):
        from urllib.parse import quote

        from django.utils import timezone

        cutoff = timezone.now()
        fresh = make_submission(developer=self.dev_a, title="Post Cutoff Idea")

        after = self._ids("?created_after=" + quote(cutoff.isoformat()))
        self.assertIn(str(fresh.id), after)
        self.assertNotIn(str(self.a_queued.id), after)

    def test_payload_is_exposed_verbatim_to_admins(self):
        response = self.client.get(_detail(self.a_queued.id))
        self.assertEqual(response.data["payload"], self.a_queued.payload)
        self.assertEqual(response.data["developer_email"], "a@studio.io")


class DecisionTests(APITestCase):
    def setUp(self):
        self.superadmin = make_user(role=UserRole.SUPER_ADMIN)
        self.client.force_authenticate(self.superadmin)
        self.account, _raw = make_approved_developer()
        self.reason = make_rejection_reason(label="Prohibited subject")

    def _decide(self, submission, action, body=None):
        return self.client.post(
            f"{QUEUE_URL}{submission.id}/{action}/", body or {}, format="json"
        )

    def test_approve_fires_webhook_immediately(self):
        submission = make_submission(developer=self.account)

        response = self._decide(submission, "approve")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        submission.refresh_from_db()
        self.assertEqual(submission.status, SubmissionStatus.APPROVED)
        self.assertEqual(submission.decided_by, self.superadmin)
        event = WebhookEvent.objects.get(submission=submission)
        self.assertEqual(event.event_type, WebhookEventType.SUBMISSION_APPROVED)

    def test_reject_requires_active_reason_label(self):
        submission = make_submission(developer=self.account)

        missing = self._decide(submission, "reject")
        unknown = self._decide(submission, "reject", {"rejection_reason": "Nope"})
        good = self._decide(submission, "reject", {"rejection_reason": "Prohibited subject"})

        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(unknown.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(good.status_code, status.HTTP_200_OK)
        submission.refresh_from_db()
        self.assertEqual(submission.rejection_reason, self.reason)

    def test_full_reversal_cycle_a_r_a_with_clean_metadata(self):
        submission = make_submission(developer=self.account)

        self._decide(submission, "approve")
        self._decide(submission, "reject", {"rejection_reason": "Prohibited subject", "rejection_note": "dup"})
        self._decide(submission, "approve")

        submission.refresh_from_db()
        self.assertEqual(submission.status, SubmissionStatus.APPROVED)
        self.assertIsNone(submission.rejection_reason)
        self.assertEqual(submission.rejection_note, "")
        events = WebhookEvent.objects.filter(submission=submission).order_by(
            "created_datetime"
        )
        self.assertEqual(
            [e.event_type for e in events],
            [
                WebhookEventType.SUBMISSION_APPROVED,
                WebhookEventType.SUBMISSION_REJECTED,
                WebhookEventType.SUBMISSION_APPROVED,
            ],
        )

    def test_reversal_unpublishes_resulting_course_but_keeps_link(self):
        course = make_draft_course(status=CourseStatus.PUBLISHED)
        submission = make_decided_submission(
            developer=self.account, approved=True, resulting_course=course
        )

        response = self._decide(
            submission, "reject", {"rejection_reason": "Prohibited subject"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        submission.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.NEEDS_REVISION)  # parked, not deleted
        self.assertEqual(submission.resulting_course, course)  # link survives for relink

        # Re-approval finds the same course again.
        reapproval = self._decide(submission, "approve")
        self.assertEqual(reapproval.status_code, status.HTTP_200_OK)
        submission.refresh_from_db()
        self.assertEqual(submission.resulting_course, course)


class SignalAndBypassTests(APITestCase):
    def setUp(self):
        self.superadmin = make_user(role=UserRole.SUPER_ADMIN)
        self.client.force_authenticate(self.superadmin)
        self.account, _raw = make_approved_developer()
        self.submission = make_submission(developer=self.account)

    def test_signals_validate_score_range_and_persist(self):
        bad = self.client.post(
            f"{QUEUE_URL}{self.submission.id}/signals/",
            {"demand_score": 150},
            format="json",
        )
        good = self.client.post(
            f"{QUEUE_URL}{self.submission.id}/signals/",
            {"demand_score": 87, "estimated_monthly_earnings": "4200.00"},
            format="json",
        )

        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(good.status_code, status.HTTP_200_OK)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.demand_score, 87)
        self.assertEqual(str(self.submission.estimated_monthly_earnings), "4200.00")

    def test_signals_fire_no_webhook(self):
        before = WebhookEvent.objects.count()
        self.client.post(
            f"{QUEUE_URL}{self.submission.id}/signals/", {"demand_score": 10}, format="json"
        )
        self.assertEqual(WebhookEvent.objects.count(), before)

    def test_bypass_toggle_fires_dedicated_event_each_way(self):
        on = self.client.post(
            f"{QUEUE_URL}{self.submission.id}/payout_bypass/",
            {"payout_bypass": True},
            format="json",
        )
        redundant = self.client.post(
            f"{QUEUE_URL}{self.submission.id}/payout_bypass/",
            {"payout_bypass": True},
            format="json",
        )
        off = self.client.post(
            f"{QUEUE_URL}{self.submission.id}/payout_bypass/",
            {"payout_bypass": False},
            format="json",
        )

        self.assertEqual(on.status_code, status.HTTP_200_OK)
        self.assertEqual(redundant.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(off.status_code, status.HTTP_200_OK)
        bypass_events = WebhookEvent.objects.filter(
            event_type=WebhookEventType.SUBMISSION_PAYOUT_BYPASS_UPDATED
        ).order_by("created_datetime")
        self.assertEqual(bypass_events.count(), 2)
        payloads = [e.payload["submission"]["payout_bypass"] for e in bypass_events]
        self.assertEqual(payloads, [True, False])


class DecisionEventWireShapeTests(APITestCase):
    """Every event-producing path must store the envelope the dispatcher
    reads. render_body only wires `payload["submission"]` onto the
    network, so a flat payload here silently delivers an empty object to
    the developer."""

    def setUp(self):
        self.superadmin = make_user(role=UserRole.SUPER_ADMIN)
        self.client.force_authenticate(self.superadmin)
        self.account, _raw = make_approved_developer()
        self.reason = make_rejection_reason(label="Prohibited subject")

    def _wire_body(self, submission, event_type):
        event = WebhookEvent.objects.filter(
            submission=submission, event_type=event_type
        ).latest("created_datetime")
        return json.loads(render_body(event))

    def test_approval_delivers_a_populated_submission(self):
        submission = make_submission(developer=self.account)

        self.client.post(f"{QUEUE_URL}{submission.id}/approve/", {}, format="json")

        body = self._wire_body(submission, WebhookEventType.SUBMISSION_APPROVED)
        submission.refresh_from_db()
        self.assertEqual(
            body["submission"],
            {
                "reference": submission.public_reference,
                "status": SubmissionStatus.APPROVED,
                "title": submission.title,
            },
        )

    def test_rejection_delivers_reason_and_note_on_the_wire(self):
        submission = make_submission(developer=self.account)

        self.client.post(
            f"{QUEUE_URL}{submission.id}/reject/",
            {"rejection_reason": "Prohibited subject", "rejection_note": "Off-topic."},
            format="json",
        )

        body = self._wire_body(submission, WebhookEventType.SUBMISSION_REJECTED)
        self.assertEqual(body["submission"]["status"], SubmissionStatus.REJECTED)
        self.assertEqual(body["submission"]["rejection_reason"], "Prohibited subject")
        self.assertEqual(body["submission"]["rejection_note"], "Off-topic.")

    def test_payout_bypass_delivers_the_flag_on_the_wire(self):
        submission = make_submission(developer=self.account)

        self.client.post(
            f"{QUEUE_URL}{submission.id}/payout_bypass/",
            {"payout_bypass": True},
            format="json",
        )

        body = self._wire_body(
            submission, WebhookEventType.SUBMISSION_PAYOUT_BYPASS_UPDATED
        )
        self.assertTrue(body["submission"]["payout_bypass"])
        self.assertEqual(body["submission"]["title"], submission.title)

    def test_ingestion_and_decision_events_share_one_envelope(self):
        submission = make_submission(developer=self.account)
        submission_service.record_event(submission)
        self.client.post(f"{QUEUE_URL}{submission.id}/approve/", {}, format="json")

        queued = self._wire_body(submission, WebhookEventType.SUBMISSION_QUEUED)
        approved = self._wire_body(submission, WebhookEventType.SUBMISSION_APPROVED)

        self.assertEqual(set(queued), set(approved))
        self.assertEqual(set(queued["submission"]), set(approved["submission"]))


class RejectionReasonTaxonomyTests(APITestCase):
    def setUp(self):
        self.superadmin = make_user(role=UserRole.SUPER_ADMIN)
        self.client.force_authenticate(self.superadmin)

    def test_create_list_and_soft_deactivate(self):
        created = self.client.post(
            REASONS_URL, {"label": "  Duplicate of existing catalog  "}, format="json"
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(created.data["label"], "Duplicate of existing catalog")

        deactivated = self.client.patch(
            f"{REASONS_URL}{created.data['id']}/", {"is_active": False}, format="json"
        )
        self.assertEqual(deactivated.data["is_active"], False)

        listing = self.client.get(REASONS_URL, {"is_active": "false"})
        results = listing.data["data"]["results"]
        self.assertIn(created.data["id"], [r["id"] for r in results])

    def test_duplicate_label_rejected(self):
        make_rejection_reason(label="Taken")
        response = self.client.post(REASONS_URL, {"label": "Taken"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
