"""The MIE course push: an approved idea's developer sends the finished course.

Every test goes through the literal URL a developer calls, authenticated
with a real API key, and asserts on the fields a developer reads.
"""

from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from api.catalog.enums import CategoryStatus
from api.courses.enums import CourseSourceType, CourseStatus, DifficultyLevel
from api.courses.models import (
    Assessment,
    Course,
    CourseVersion,
    Lesson,
    LessonContentBlock,
    LessonRequirement,
    Module,
)
from api.courses.tests.factories import make_category, make_questions, make_user
from api.mie.enums import DeveloperAccountStatus, SubmissionStatus, WebhookEventType
from api.mie.models import WebhookEvent
from api.mie.services import developer_service
from api.mie.tests.factories import (
    make_approved_developer,
    make_decided_submission,
    make_submission,
)
from api.reviews.enums import ReviewActionType
from api.reviews.models import ReviewAction
from api.users.enums import UserRole

QUEUE_URL = "/api/v1/review-queue/"


def _push_url(submission_id) -> str:
    return f"/api/v1/mie/v1/submissions/{submission_id}/course/"


def _course_body(*, title, category, version, modules=4, lessons=3, **overrides):
    """A push body that passes the structural check at the platform's
    default thresholds: text-only lessons, an external preview video."""

    body = {
        "title": title,
        "description": "word " * 150,
        "category": str(category.id),
        "version": str(version.id),
        "difficulty_level": DifficultyLevel.INTERMEDIATE,
        "preview_video_url": "https://videos.studio.io/preview.mp4",
        "learning_objectives": [f"Course objective {i}" for i in range(1, 6)],
        "terms_accepted": True,
        "modules": [
            {
                "title": f"Module {m}",
                "learning_objectives": ["Module objective"],
                "lessons": [
                    {
                        "title": f"Lesson {m}-{lesson}",
                        "lesson_type": "TEXT",
                        "script": "word " * 600,
                        "learning_objectives": ["Objective 1", "Objective 2"],
                        "duration_minutes": 20,
                    }
                    for lesson in range(1, lessons + 1)
                ],
                "assessment": {
                    "title": f"Module {m} quiz",
                    "questions": make_questions(3),
                },
            }
            for m in range(1, modules + 1)
        ],
        "final_assessment": {"title": "Final exam", "questions": make_questions(15)},
    }
    body.update(overrides)
    return body


class CoursePushTestCase(APITestCase):
    def setUp(self):
        self.developer, self.key = make_approved_developer()
        self.category = make_category(
            creator_price_intermediate=Decimal("150.00"), status=CategoryStatus.ACTIVE
        )
        self.version, _ = CourseVersion.objects.get_or_create(label="1.0")
        self.idea = make_decided_submission(
            developer=self.developer, title="Build a Production-Grade Rust Course"
        )

    def _push(self, submission=None, key=None, **overrides):
        submission = submission or self.idea
        body = _course_body(
            title=submission.title, category=self.category, version=self.version
        )
        body.update(overrides)
        return self.client.post(
            _push_url(submission.id),
            body,
            format="json",
            HTTP_X_MIE_API_KEY=key or self.key,
        )

    def _get(self, submission=None, key=None):
        submission = submission or self.idea
        return self.client.get(
            _push_url(submission.id), HTTP_X_MIE_API_KEY=key or self.key
        )

    def _error_codes(self, response) -> list[str]:
        return [error["code"] for error in response.data["errors"]]

    def _error_messages(self, response) -> str:
        return " ".join(error["message"] for error in response.data["errors"])


class CoursePushSuccessTests(CoursePushTestCase):
    def test_push_builds_the_course_and_submits_it_for_review(self):
        response = self._push()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["submission_id"], str(self.idea.id))
        self.assertEqual(
            response.data["submission_reference"], self.idea.public_reference
        )
        self.assertEqual(response.data["status"], CourseStatus.SUBMITTED)
        self.assertEqual(response.data["module_count"], 4)
        self.assertEqual(response.data["lesson_count"], 12)
        self.assertIsNotNone(response.data["submitted_at"])
        self.assertIsNone(response.data["revision_feedback"])

        course = Course.objects.get(id=response.data["course_id"])
        self.idea.refresh_from_db()
        self.developer.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.SUBMITTED)
        self.assertEqual(course.source_type, CourseSourceType.DEVELOPER_API)
        self.assertEqual(course.creator, self.developer.creator_user)
        self.assertEqual(self.idea.resulting_course, course)
        self.assertEqual(course.version, self.version)
        # Priced like a creator's course, at the category's price for its level.
        self.assertEqual(course.creator_price_snapshot, Decimal("150.00"))

    def test_the_draft_minimum_hold_does_not_apply(self):
        # The platform default hold is 48 hours; the push submits at once.
        response = self._push()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_every_level_of_the_schema_is_stored(self):
        body = _course_body(
            title=self.idea.title, category=self.category, version=self.version
        )
        lesson = body["modules"][0]["lessons"][0]
        lesson["lesson_requirement"] = "Know basic Rust syntax."
        lesson["content_blocks"] = [
            {"block_type": "HEADING_1", "text_content": "Ownership"},
            {"block_type": "IMAGE", "media_url": "https://cdn.studio.io/diagram.png"},
            {"block_type": "DIVIDER"},
        ]
        lesson["assessment"] = {"title": "Lesson quiz", "questions": make_questions(2)}

        response = self.client.post(
            _push_url(self.idea.id), body, format="json", HTTP_X_MIE_API_KEY=self.key
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        stored = Lesson.objects.get(
            module__course_id=response.data["course_id"], title="Lesson 1-1"
        )
        self.assertEqual(
            list(stored.content_blocks.values_list("order", "block_type")),
            [(1, "HEADING_1"), (2, "IMAGE"), (3, "DIVIDER")],
        )
        self.assertEqual(
            list(
                LessonRequirement.objects.filter(lesson=stored).values_list(
                    "text", flat=True
                )
            ),
            ["Know basic Rust syntax."],
        )
        self.assertEqual(stored.assessment.title, "Lesson quiz")
        self.assertEqual(
            list(
                Module.objects.filter(course_id=response.data["course_id"])
                .order_by("order")
                .values_list("order", flat=True)
            ),
            [1, 2, 3, 4],
        )
        self.assertEqual(
            Assessment.objects.get(course_id=response.data["course_id"]).title,
            "Final exam",
        )

    def test_video_lessons_take_any_https_host_and_text_lessons_need_none(self):
        body = _course_body(
            title=self.idea.title, category=self.category, version=self.version
        )
        lessons = body["modules"][0]["lessons"]
        lessons[0].update(lesson_type="VIDEO", video_url="https://cdn.studio.io/l1.mp4")
        lessons[1].update(
            lesson_type="VIDEO", embedded_link="https://player.vimeo.com/video/123"
        )

        response = self.client.post(
            _push_url(self.idea.id), body, format="json", HTTP_X_MIE_API_KEY=self.key
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(
            Lesson.objects.filter(
                module__course_id=response.data["course_id"], content_type="VIDEO"
            ).count(),
            2,
        )

    def test_title_matches_ignoring_case_and_spaces_and_the_idea_title_is_kept(self):
        response = self._push(title=f"  {self.idea.title.upper()}  ")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["title"], self.idea.title)

    def test_a_course_submitted_webhook_carries_the_course(self):
        response = self._push()

        event = WebhookEvent.objects.get(
            submission=self.idea, event_type=WebhookEventType.COURSE_SUBMITTED
        )
        course = event.payload["submission"]["course"]
        self.assertEqual(course["id"], response.data["course_id"])
        self.assertEqual(course["status"], CourseStatus.SUBMITTED)
        self.assertEqual(
            event.payload["submission"]["reference"], self.idea.public_reference
        )

    def test_no_payout_is_scheduled_for_a_pushed_course(self):
        from api.payments.services.transaction_services import effect_course_payment

        self._push()
        course = Course.objects.get(mie_submission=self.idea)

        with patch(
            "api.payments.services.transaction_services.release_course_payment"
        ) as task:
            effect_course_payment(course)

        task.apply_async.assert_not_called()


class LinkedCreatorAccountTests(CoursePushTestCase):
    def test_one_linked_account_owns_every_course_of_a_developer(self):
        second_idea = make_decided_submission(
            developer=self.developer, title="Async Rust"
        )

        self.assertEqual(self._push().status_code, status.HTTP_201_CREATED)
        self.assertEqual(self._push(second_idea).status_code, status.HTTP_201_CREATED)

        self.developer.refresh_from_db()
        creator = self.developer.creator_user
        self.assertEqual(Course.objects.filter(creator=creator).count(), 2)
        self.assertEqual(creator.role, UserRole.COURSE_CREATOR)
        self.assertTrue(
            creator.email.endswith(f"@{developer_service.LINKED_CREATOR_EMAIL_DOMAIN}")
        )

    def test_the_linked_account_can_never_sign_in(self):
        self._push()
        self.developer.refresh_from_db()
        creator = self.developer.creator_user

        self.assertFalse(creator.has_usable_password())
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": creator.email, "password": ""},
            format="json",
        )
        self.assertNotEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("access", response.data)

    def test_an_existing_platform_user_with_the_developer_email_is_never_linked(self):
        platform_user = make_user(email=self.developer.email)

        self._push()

        self.developer.refresh_from_db()
        self.assertNotEqual(self.developer.creator_user, platform_user)
        self.assertFalse(Course.objects.filter(creator=platform_user).exists())


class CoursePushGateTests(CoursePushTestCase):
    def _assert_nothing_stored(self):
        self.assertFalse(Course.objects.exists())
        self.developer.refresh_from_db()
        self.assertIsNone(self.developer.creator_user)

    def test_requires_credentials(self):
        response = self.client.post(_push_url(self.idea.id), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_suspended_developer_is_refused(self):
        self.developer.status = DeveloperAccountStatus.SUSPENDED
        self.developer.save(update_fields=["status"])

        response = self._push()

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self._assert_nothing_stored()

    def test_another_developers_idea_is_not_found(self):
        _other, other_key = make_approved_developer()

        response = self._push(key=other_key)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(Course.objects.exists())

    def test_an_unknown_idea_is_not_found(self):
        response = self.client.post(
            _push_url("00000000-0000-4000-8000-000000000000"),
            _course_body(title="x", category=self.category, version=self.version),
            format="json",
            HTTP_X_MIE_API_KEY=self.key,
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_idea_that_is_not_approved_is_refused(self):
        for idea_status in (
            SubmissionStatus.PENDING_REVIEW,
            SubmissionStatus.REJECTED,
            SubmissionStatus.DUPLICATE_IN_QUEUE,
            SubmissionStatus.DUPLICATE_EXISTING,
            SubmissionStatus.PREVIOUSLY_REJECTED,
        ):
            with self.subTest(status=idea_status):
                idea = make_submission(
                    developer=self.developer,
                    status=idea_status,
                    decided_at=self.idea.decided_at,
                )

                response = self._push(idea)

                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assertEqual(self._error_codes(response), ["idea_not_approved"])
        self._assert_nothing_stored()

    def test_a_different_title_is_refused_with_the_expected_one(self):
        response = self._push(title="Rust for Beginners")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "title")
        self.assertIn(self.idea.title, self._error_messages(response))
        self._assert_nothing_stored()

    def test_the_ideas_category_is_enforced(self):
        self.idea.category = make_category()
        self.idea.save(update_fields=["category"])

        response = self._push()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "category")
        self._assert_nothing_stored()

    def test_an_inactive_category_is_refused(self):
        self.category.status = CategoryStatus.INACTIVE
        self.category.save(update_fields=["status"])

        response = self._push()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "category")
        self._assert_nothing_stored()

    def test_terms_must_be_accepted(self):
        response = self._push(terms_accepted=False)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "terms_accepted")
        self._assert_nothing_stored()


class CoursePushAllOrNothingTests(CoursePushTestCase):
    def test_a_course_failing_the_structural_check_is_not_stored_and_every_failure_is_listed(
        self,
    ):
        body = _course_body(
            title=self.idea.title,
            category=self.category,
            version=self.version,
            modules=1,
            preview_video_url="",
        )

        response = self.client.post(
            _push_url(self.idea.id), body, format="json", HTTP_X_MIE_API_KEY=self.key
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        fields = {error["field_name"] for error in response.data["errors"]}
        self.assertEqual(fields, {"structural_standards"})
        messages = self._error_messages(response)
        self.assertIn("preview video", messages)
        self.assertIn("modules", messages)
        self.assertFalse(Course.objects.exists())
        self.assertFalse(Module.objects.exists())
        self.assertFalse(LessonContentBlock.objects.exists())
        self.assertFalse(WebhookEvent.objects.filter(submission=self.idea).exists())
        self.developer.refresh_from_db()
        self.assertIsNone(self.developer.creator_user)
        self.idea.refresh_from_db()
        self.assertIsNone(self.idea.resulting_course)

    def test_a_quiz_block_is_refused(self):
        body = _course_body(
            title=self.idea.title, category=self.category, version=self.version
        )
        body["modules"][0]["lessons"][0]["content_blocks"] = [{"block_type": "QUIZ"}]

        response = self.client.post(
            _push_url(self.idea.id), body, format="json", HTTP_X_MIE_API_KEY=self.key
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("assessment", self._error_messages(response))
        self.assertFalse(Course.objects.exists())

    def test_a_video_lesson_without_media_is_refused(self):
        body = _course_body(
            title=self.idea.title, category=self.category, version=self.version
        )
        body["modules"][0]["lessons"][0]["lesson_type"] = "VIDEO"

        response = self.client.post(
            _push_url(self.idea.id), body, format="json", HTTP_X_MIE_API_KEY=self.key
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("video_url or embedded_link", self._error_messages(response))

    def test_duplicate_orders_are_refused_before_anything_is_written(self):
        body = _course_body(
            title=self.idea.title, category=self.category, version=self.version
        )
        for module in body["modules"]:
            module["order"] = 1

        response = self.client.post(
            _push_url(self.idea.id), body, format="json", HTTP_X_MIE_API_KEY=self.key
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("distinct order", self._error_messages(response))
        self.assertFalse(Course.objects.exists())

    def test_a_push_over_the_module_cap_is_refused(self):
        from api.mie.serializers.course_push_serializer import MAX_PUSH_MODULES

        body = _course_body(
            title=self.idea.title,
            category=self.category,
            version=self.version,
            modules=MAX_PUSH_MODULES + 1,
            lessons=1,
        )

        response = self.client.post(
            _push_url(self.idea.id), body, format="json", HTTP_X_MIE_API_KEY=self.key
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(response.data["errors"][0]["field_name"].startswith("modules"))


class CoursePushRevisionTests(CoursePushTestCase):
    def _reject_in_content_review(self, course_id):
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.client.force_authenticate(reviewer)
        self.client.post(f"{QUEUE_URL}{course_id}/claim/")
        lesson = (
            Lesson.objects.filter(module__course_id=course_id)
            .order_by("module__order", "order")
            .first()
        )
        response = self.client.post(
            f"{QUEUE_URL}{course_id}/reject/",
            {
                "feedback": {"summary": "Module 1 needs worked examples."},
                "flags": [
                    {
                        "flag_type": "script_length",
                        "title": "Script too short",
                        "reviewer_note": "Add a second example.",
                        "lesson_id": str(lesson.id),
                    }
                ],
            },
            format="json",
        )
        self.client.force_authenticate(None)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_pushing_again_while_in_review_is_refused(self):
        self.assertEqual(self._push().status_code, status.HTTP_201_CREATED)

        response = self._push()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(self._error_codes(response), ["course_in_review"])
        self.assertEqual(Course.objects.count(), 1)

    def test_a_content_rejection_sends_the_feedback_and_a_repush_replaces_the_course(
        self,
    ):
        first = self._push()
        course_id = first.data["course_id"]

        self._reject_in_content_review(course_id)

        event = WebhookEvent.objects.get(
            submission=self.idea, event_type=WebhookEventType.COURSE_REVISION_REQUESTED
        )
        sent = event.payload["submission"]["course"]
        self.assertEqual(sent["status"], CourseStatus.DRAFT)
        self.assertEqual(
            sent["revision_feedback"]["feedback"]["summary"],
            "Module 1 needs worked examples.",
        )
        self.assertEqual(
            sent["revision_feedback"]["flags"][0]["lesson_title"], "Lesson 1-1"
        )

        status_response = self._get()
        self.assertEqual(status_response.status_code, status.HTTP_200_OK)
        self.assertEqual(status_response.data["status"], CourseStatus.DRAFT)
        self.assertEqual(
            status_response.data["revision_feedback"]["flags"][0]["title"],
            "Script too short",
        )

        revised = _course_body(
            title=self.idea.title,
            category=self.category,
            version=self.version,
            modules=5,
        )
        revised["modules"][0]["title"] = "Module 1, with worked examples"
        response = self.client.post(
            _push_url(self.idea.id), revised, format="json", HTTP_X_MIE_API_KEY=self.key
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["course_id"], course_id)
        self.assertEqual(response.data["status"], CourseStatus.SUBMITTED)
        self.assertEqual(response.data["module_count"], 5)
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(
            Module.objects.filter(course_id=course_id).order_by("order").first().title,
            "Module 1, with worked examples",
        )
        # The earlier review round survives the replacement.
        self.assertTrue(
            ReviewAction.objects.filter(
                course_id=course_id, action=ReviewActionType.REJECT
            ).exists()
        )
        self.assertEqual(
            WebhookEvent.objects.filter(
                submission=self.idea, event_type=WebhookEventType.COURSE_SUBMITTED
            ).count(),
            2,
        )

    def test_a_qa_rejection_also_requests_a_revision(self):
        course_id = self._push().data["course_id"]
        Course.objects.filter(id=course_id).update(status=CourseStatus.QA_VERIFICATION)
        self.client.force_authenticate(make_user(role=UserRole.QA_REVIEWER))
        self.client.post(f"{QUEUE_URL}{course_id}/qa-claim/")

        response = self.client.post(
            f"{QUEUE_URL}{course_id}/qa-reject/",
            {"feedback": {"summary": "Preview video has no captions."}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        event = WebhookEvent.objects.get(
            submission=self.idea, event_type=WebhookEventType.COURSE_REVISION_REQUESTED
        )
        self.assertEqual(
            event.payload["submission"]["course"]["revision_feedback"]["stage"], "QA"
        )

    def test_publishing_sends_course_published(self):
        course_id = self._push().data["course_id"]
        Course.objects.filter(id=course_id).update(status=CourseStatus.APPROVED)
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

        response = self.client.post(
            f"{QUEUE_URL}{course_id}/publish/", {}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        event = WebhookEvent.objects.get(
            submission=self.idea, event_type=WebhookEventType.COURSE_PUBLISHED
        )
        self.assertEqual(
            event.payload["submission"]["course"]["status"], CourseStatus.PUBLISHED
        )

        self.client.force_authenticate(None)
        self.assertEqual(self._push().status_code, status.HTTP_409_CONFLICT)

    def test_a_creators_course_moving_through_review_sends_no_webhook(self):
        from api.courses.tests.factories import build_compliant_course
        from api.courses.services import course_service

        creator = make_user()
        course = course_service.submit_course(
            course=build_compliant_course(creator=creator, category=self.category),
            actor=creator,
        )

        self._reject_in_content_review(course.id)

        self.assertFalse(WebhookEvent.objects.exists())

    def test_the_webhook_hook_costs_a_creators_course_no_query(self):
        # The hook runs on every creator rejection and publication, so for
        # anything that did not come in through the push it must return
        # before touching the database.
        from api.courses.tests.factories import make_draft_course
        from api.mie.services import course_push_service

        course = make_draft_course(creator=make_user(), category=self.category)

        with self.assertNumQueries(0):
            course_push_service.record_course_event(
                course=course, event_type=WebhookEventType.COURSE_PUBLISHED
            )

    def test_a_reversed_idea_blocks_a_repush_but_leaves_the_course(self):
        course_id = self._push().data["course_id"]
        self._reject_in_content_review(course_id)
        self.idea.status = SubmissionStatus.REJECTED
        self.idea.save(update_fields=["status"])

        response = self._push()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(self._error_codes(response), ["idea_not_approved"])
        self.assertEqual(Course.objects.get(id=course_id).status, CourseStatus.DRAFT)


class CourseStatusApiTests(CoursePushTestCase):
    def test_not_found_before_a_course_is_pushed(self):
        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_another_developers_idea_is_not_found(self):
        self._push()
        _other, other_key = make_approved_developer()

        response = self._get(key=other_key)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_requires_credentials(self):
        response = self.client.get(_push_url(self.idea.id))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_shows_the_course_after_a_push(self):
        course_id = self._push().data["course_id"]

        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["course_id"], course_id)
        self.assertEqual(response.data["status"], CourseStatus.SUBMITTED)
        self.assertEqual(response.data["lesson_count"], 12)
        self.assertIsNone(response.data["revision_feedback"])


class CoursePushQueryCountTests(CoursePushTestCase):
    def _queries_to_build(self, *, modules, lessons, idea):
        body = _course_body(
            title=idea.title,
            category=self.category,
            version=self.version,
            modules=modules,
            lessons=lessons,
        )
        for module in body["modules"]:
            for lesson in module["lessons"]:
                lesson["duration_minutes"] = 240 // (modules * lessons)
                lesson["content_blocks"] = [
                    {"block_type": "PARAGRAPH", "text_content": "Intro"},
                    {"block_type": "PARAGRAPH", "text_content": "Body"},
                ]
        # Everything up to building the tree is identical for both sizes;
        # what is compared is the whole request.
        with CaptureQueriesContext(connection) as context:
            response = self.client.post(
                _push_url(idea.id), body, format="json", HTTP_X_MIE_API_KEY=self.key
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return len(context.captured_queries)

    def test_building_the_content_does_not_query_per_row(self):
        # Warm the linked account so both pushes do the same fixed work.
        self._push()
        small = make_decided_submission(developer=self.developer, title="Small course")
        large = make_decided_submission(developer=self.developer, title="Large course")

        small_count = self._queries_to_build(modules=4, lessons=3, idea=small)
        large_count = self._queries_to_build(modules=6, lessons=5, idea=large)

        self.assertEqual(small_count, large_count)


class UploadPresignApiTests(APITestCase):
    URL = "/api/v1/mie/v1/uploads/presign/"

    def setUp(self):
        _developer, self.key = make_approved_developer()

    @patch("shared.services.storage_service._get_s3_client")
    def test_returns_a_signed_upload_and_the_durable_media_url(self, get_client):
        get_client.return_value.generate_presigned_url.return_value = (
            "https://storage.example.com/signed"
        )

        response = self.client.post(
            self.URL,
            {
                "filename": "preview.mp4",
                "content_type": "video/mp4",
                "purpose": "COURSE_PREVIEW_VIDEO",
                "size": 48_000_000,
                "width": 1920,
                "height": 1080,
                "codec": "h264",
                "duration_seconds": 90,
            },
            format="json",
            HTTP_X_MIE_API_KEY=self.key,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(
            response.data["upload_url"], "https://storage.example.com/signed"
        )
        self.assertTrue(response.data["file_key"].startswith("uploads/courses/"))
        self.assertTrue(response.data["media_url"].endswith(response.data["file_key"]))
        self.assertEqual(
            response.data["upload_headers"]["x-amz-meta-upload-purpose"],
            "COURSE_PREVIEW_VIDEO",
        )

    def test_the_creator_rules_apply(self):
        response = self.client.post(
            self.URL,
            {
                "filename": "preview.mp4",
                "content_type": "video/mp4",
                "purpose": "COURSE_PREVIEW_VIDEO",
                "size": 48_000_000,
                "width": 1920,
                "height": 1080,
                "codec": "vp9",
                "duration_seconds": 90,
            },
            format="json",
            HTTP_X_MIE_API_KEY=self.key,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("H264", " ".join(e["message"] for e in response.data["errors"]))

    def test_only_course_media_purposes_are_offered(self):
        response = self.client.post(
            self.URL,
            {
                "filename": "outline.pdf",
                "content_type": "application/pdf",
                "purpose": "COURSE_DOCUMENT_IMPORT",
                "size": 1000,
            },
            format="json",
            HTTP_X_MIE_API_KEY=self.key,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "purpose")

    def test_requires_credentials(self):
        response = self.client.post(self.URL, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CourseRequirementsApiTests(APITestCase):
    URL = "/api/v1/mie/v1/course-requirements/"

    def test_serves_live_rules_and_the_ids_a_push_needs(self):
        _developer, key = make_approved_developer()
        active = make_category(status=CategoryStatus.ACTIVE)
        inactive = make_category(status=CategoryStatus.INACTIVE)
        version, _ = CourseVersion.objects.get_or_create(label="1.0")

        response = self.client.get(self.URL, HTTP_X_MIE_API_KEY=key)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        category_ids = {row["id"] for row in response.data["categories"]}
        self.assertIn(str(active.id), category_ids)
        self.assertNotIn(str(inactive.id), category_ids)
        self.assertIn(
            str(version.id), {row["id"] for row in response.data["course_versions"]}
        )
        rules = response.data["structural_rules"]
        self.assertEqual(rules["modules_per_course"], {"min": 4, "max": 12})
        self.assertTrue(rules["preview_video_required"])
        self.assertNotIn("QUIZ", response.data["choices"]["content_block_type"])
        self.assertEqual(
            {row["purpose"] for row in response.data["upload_purposes"]},
            {
                "COURSE_PREVIEW_VIDEO",
                "LESSON_VIDEO",
                "LESSON_IMAGE",
                "COURSE_THUMBNAIL",
                "SUBTITLE",
            },
        )

    def test_requires_credentials(self):
        response = self.client.get(self.URL)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
