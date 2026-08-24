from django.db import IntegrityError
from django.test import TestCase

from api.mie.enums import (
    DeveloperAccountStatus,
    SubmissionStatus,
    WebhookEventType,
)
from api.mie.models import CourseSubmission, DeveloperAccount, WebhookEvent
from api.mie.tests.factories import (
    make_approved_developer,
    make_decided_submission,
    make_developer_account,
    make_submission,
    make_webhook_event,
)


class DeveloperAccountModelTests(TestCase):
    def test_registration_defaults_to_pending_without_key_material(self):
        account = make_developer_account()

        self.assertEqual(account.status, DeveloperAccountStatus.PENDING)
        self.assertEqual(account.api_key_hash, "")
        self.assertEqual(account.signing_secret, "")
        self.assertEqual(account.plan_type, "PAID_PER_SUBMISSION")

    def test_email_is_unique_at_db_level(self):
        make_developer_account(email="dev@example.com")

        with self.assertRaises(IntegrityError):
            make_developer_account(email="dev@example.com")

    def test_key_hash_requires_active_status(self):
        with self.assertRaises(IntegrityError):
            make_developer_account(api_key_prefix="scb_live_abc", api_key_hash="0" * 64)

    def test_secret_allowed_once_suspended(self):
        account = make_developer_account(
            status=DeveloperAccountStatus.SUSPENDED, signing_secret="s" * 32
        )

        account.full_clean(exclude=["webhook_url"])
        self.assertEqual(account.status, DeveloperAccountStatus.SUSPENDED)


class SubmissionReferenceTests(TestCase):
    def test_public_reference_suffix_tracks_status(self):
        cases = {
            SubmissionStatus.PENDING_REVIEW: "P",
            SubmissionStatus.DUPLICATE_IN_QUEUE: "D",
            SubmissionStatus.DUPLICATE_EXISTING: "E",
            SubmissionStatus.PREVIOUSLY_REJECTED: "X",
            SubmissionStatus.APPROVED: "A",
            SubmissionStatus.REJECTED: "R",
        }
        submission = make_submission()

        for status, suffix in cases.items():
            submission.status = status
            self.assertTrue(
                submission.public_reference.endswith(f"-{suffix}"),
                f"{status} should surface -{suffix}",
            )
            self.assertTrue(submission.public_reference.startswith("SCB-"))

    def test_reference_is_stable_for_same_state(self):
        submission = make_submission()

        self.assertEqual(submission.public_reference, submission.public_reference)


class SubmissionQueueConstraintTests(TestCase):
    def test_only_one_pending_review_per_title(self):
        title = "Build a Rust Course"
        make_submission(title=title)

        with self.assertRaises(IntegrityError):
            make_submission(title=title)

    def test_same_title_allowed_in_non_queue_states(self):
        title = "Build a Rust Course"
        make_decided_submission(title=title, approved=True)
        second = make_submission(title=title)

        self.assertEqual(second.status, SubmissionStatus.PENDING_REVIEW)

    def test_decided_statuses_require_decision_timestamp(self):
        from django.db import transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            make_submission(status=SubmissionStatus.APPROVED)

    def test_reversal_clears_then_refires_is_representable(self):
        submission = make_decided_submission(approved=True)

        submission.status = SubmissionStatus.REJECTED
        submission.save(update_fields=["status", "updated_datetime"])

        submission.refresh_from_db()
        self.assertEqual(submission.status, SubmissionStatus.REJECTED)
        self.assertEqual(CourseSubmission.objects.count(), 1)


class WebhookEventModelTests(TestCase):
    def test_event_creation_defaults(self):
        event = make_webhook_event()

        self.assertEqual(event.delivery_status, "PENDING")
        self.assertEqual(event.attempts, 0)
        self.assertIn(event.event_type, WebhookEventType.values)

    def test_events_ordered_newest_first_per_submission(self):
        developer, _ = make_approved_developer()
        submission = make_submission(developer=developer)
        older = make_webhook_event(
            submission=submission, event_type=WebhookEventType.SUBMISSION_QUEUED
        )
        newer = make_webhook_event(
            submission=submission, event_type=WebhookEventType.SUBMISSION_APPROVED
        )

        events = list(WebhookEvent.objects.filter(submission=submission))

        self.assertEqual(events[0], newer)
        self.assertEqual(events[-1], older)


class CascadeTests(TestCase):
    def test_deleting_developer_cascades_submissions_and_events(self):
        developer, _ = make_approved_developer()
        submission = make_submission(developer=developer)
        make_webhook_event(submission=submission)

        DeveloperAccount.objects.filter(id=developer.id).delete()

        self.assertFalse(CourseSubmission.objects.exists())
