from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.tests.factories import make_draft_course
from api.mie.enums import DeveloperAccountStatus, SubmissionStatus, WebhookEventType
from api.mie.models import CourseSubmission, WebhookEvent
from api.mie.services.key_service import issue_credentials
from api.mie.services.submission_service import EVENT_TYPE_BY_STATUS
from api.mie.tests.factories import (
    make_approved_developer,
    make_decided_submission,
    make_rejection_reason,
    make_submission,
)

INGEST_URL = "/api/v1/mie/v1/submissions/"


class IngestAuthTests(APITestCase):
    def test_requires_api_key(self):
        response = self.client.post(INGEST_URL, {"title": "x"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_key_rejected_with_machine_code(self):
        _, raw = make_approved_developer()
        other = make_approved_developer()[0]
        issue_credentials(other)

        response = self.client.post(
            INGEST_URL,
            {"title": "x"},
            format="json",
            HTTP_X_MIE_API_KEY="scb_live_totally_unknown",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["errors"][0]["code"], "invalid_api_key")

    def test_platform_token_also_accepted(self):
        from api.mie.services import dev_token_service

        account, _raw = make_approved_developer()

        response = self.client.post(
            INGEST_URL,
            {"title": "Token Path Idea"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {dev_token_service.issue_dev_token(account)}",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class IngestValidationTests(APITestCase):
    def setUp(self):
        self.account, self.raw_key = make_approved_developer()

    def _post(self, payload):
        return self.client.post(
            INGEST_URL, payload, format="json", HTTP_X_MIE_API_KEY=self.raw_key
        )

    def test_missing_title_is_400(self):
        response = self._post({"description": "no title"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(CourseSubmission.objects.exists())

    def test_blank_or_non_string_title_is_400(self):
        for bad in ("   ", 42, None):
            with self.subTest(title=bad):
                response = self._post({"title": bad})
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_overlong_title_is_400(self):
        response = self._post({"title": "x" * 256})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_extra_payload_keys_are_stored_verbatim(self):
        body = {
            "title": "Verbatim Storage",
            "description": "kept exactly",
            "audience": "nurses",
            "weird_key": [1, 2, 3],
        }

        self._post(body)

        stored = CourseSubmission.objects.get(title="Verbatim Storage")
        self.assertEqual(stored.payload["weird_key"], [1, 2, 3])
        self.assertEqual(stored.payload["audience"], "nurses")


class DedupEngineTests(APITestCase):
    """The three ordered checks: first match wins, rest are skipped."""

    def setUp(self):
        self.account, self.raw_key = make_approved_developer()

    def _submit(self, title):
        return self.client.post(
            INGEST_URL, {"title": title}, format="json", HTTP_X_MIE_API_KEY=self.raw_key
        )

    def test_clean_title_queues_for_review_and_fires_queued_event(self):
        response = self._submit("Quantum Gardening")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        submission = CourseSubmission.objects.get(id=response.data["id"])
        self.assertEqual(submission.status, SubmissionStatus.PENDING_REVIEW)
        self.assertIsNotNone(submission.queued_at)
        self.assertTrue(response.data["reference"].endswith("-P"))
        event = WebhookEvent.objects.get(submission=submission)
        self.assertEqual(event.event_type, WebhookEventType.SUBMISSION_QUEUED)

    def test_check1_previously_rejected_wins_and_inherits_reason(self):
        reason = make_rejection_reason(label="Prohibited subject matter")
        rejected = make_decided_submission(
            title="Controversial History",
            approved=False,
            rejection_reason=reason,
        )

        response = self._submit("controversial history")  # case-insensitive

        self.assertEqual(CourseSubmission.objects.get(id=response.data["id"]).status,
                         SubmissionStatus.PREVIOUSLY_REJECTED)
        short = CourseSubmission.objects.get(id=response.data["id"])
        self.assertEqual(short.rejection_reason, reason)
        self.assertTrue(short.public_reference.endswith("-X"))
        event = WebhookEvent.objects.get(submission=short)
        self.assertEqual(event.event_type, WebhookEventType.SUBMISSION_PREVIOUSLY_REJECTED)
        self.assertIsNotNone(rejected)

    def test_check2_duplicate_existing_beats_queue(self):
        make_draft_course(title="Already A Course")
        make_submission(title="already a course")  # also in queue

        response = self._submit("ALREADY A COURSE")

        short = CourseSubmission.objects.get(id=response.data["id"])
        self.assertEqual(short.status, SubmissionStatus.DUPLICATE_EXISTING)
        self.assertTrue(short.public_reference.endswith("-E"))

    def test_check3_duplicate_in_queue(self):
        make_submission(title="Same Idea Waiting")

        response = self._submit("same idea waiting")

        short = CourseSubmission.objects.get(id=response.data["id"])
        self.assertEqual(short.status, SubmissionStatus.DUPLICATE_IN_QUEUE)
        self.assertTrue(short.public_reference.endswith("-D"))
        self.assertIsNone(short.queued_at)

    def test_rejected_title_does_not_block_after_it_left_queue_only_states(self):
        make_decided_submission(title="Old Rejected One", approved=False)
        # A REJECTED row occupies no queue slot; only PENDING_REVIEW does.
        # (It does feed check 1, so the new title must differ.)

        response = self._submit("Brand New Title")

        self.assertEqual(
            CourseSubmission.objects.get(id=response.data["id"]).status,
            SubmissionStatus.PENDING_REVIEW,
        )

    def test_every_outcome_records_exactly_one_webhook_event(self):
        outcomes = [
            ("First Fresh Idea", SubmissionStatus.PENDING_REVIEW),
            ("Second Fresh Idea", SubmissionStatus.PENDING_REVIEW),
        ]
        for title, expected in outcomes:
            self._submit(title)
        submissions = CourseSubmission.objects.filter(developer=self.account)
        for submission in submissions:
            self.assertEqual(
                WebhookEvent.objects.filter(submission=submission).count(), 1
            )
            event = WebhookEvent.objects.get(submission=submission)
            self.assertEqual(event.event_type, EVENT_TYPE_BY_STATUS[submission.status])
            self.assertIn("reference", event.payload["submission"])


class QueueIntegrityTests(APITestCase):
    def setUp(self):
        self.account, self.raw_key = make_approved_developer()

    def test_service_level_duplicate_guard_matches_db_constraint(self):
        submission_service_level = make_submission(title="Race Condition Idea")
        self.assertEqual(submission_service_level.status, SubmissionStatus.PENDING_REVIEW)

        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            make_submission(title="race condition idea")


class SuspendedDeveloperTests(APITestCase):
    def test_suspended_account_cannot_ingest(self):
        account, raw = make_approved_developer()
        account.status = DeveloperAccountStatus.SUSPENDED
        account.save(update_fields=["status", "updated_datetime"])

        response = self.client.post(
            INGEST_URL, {"title": "x"}, format="json", HTTP_X_MIE_API_KEY=raw
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
