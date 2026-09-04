from decimal import Decimal
from uuid import uuid4

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APITestCase

from api.catalog.enums import TrackPreference
from api.courses.enums import (
    CourseSourceType,
    CourseStatus,
    DistributionStatus,
)
from api.courses.models import CourseDistribution, CourseVersion
from api.reviews.enums import ReviewActionType, ReviewStage
from api.reviews.models import ReviewAction, ReviewAssignment
from api.courses.services import course_service
from api.reviews.services import review_service
from api.courses.tests.factories import build_compliant_course, make_category, make_user
from api.users.enums import UserRole
from api.users.models import UserActivityLog
from api.users.services import queue_preference_service, reviewer_availability_service
from api.wallet.services import wallet_service


class ReviewQueueApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.qa_reviewer = make_user(role=UserRole.QA_REVIEWER)
        self.admin = make_user(role=UserRole.ADMIN)
        self.category = make_category(
            creator_price_beginner=Decimal("120.00"),
            creator_price_intermediate=Decimal("120.00"),
            creator_price_advanced=Decimal("120.00"),
        )
        CourseVersion.objects.get_or_create(label="1.0")

    def _submitted_course(self, category=None):
        course = build_compliant_course(
            creator=self.creator, category=category or self.category
        )
        return course_service.submit_course(course=course, actor=self.creator)

    def test_queue_lists_submitted_and_in_review_ordered_by_submitted_at(self):
        course1 = self._submitted_course()
        course2 = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        response = self.client.get("/api/v1/review-queue/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["data"]["results"]]
        self.assertEqual(ids, [str(course1.id), str(course2.id)])

    def test_queue_filter_by_status(self):
        self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        response = self.client.get("/api/v1/review-queue/", {"status": "SUBMITTED"})
        self.assertEqual(len(response.data["data"]["results"]), 1)

    def test_queue_includes_approved_and_published_by_default_and_via_filter(self):
        submitted_course = self._submitted_course()

        approved_course = self._submitted_course()
        review_service.approve_course(course=approved_course, reviewer=self.reviewer)

        published_course = self._submitted_course()
        review_service.approve_course(course=published_course, reviewer=self.reviewer)
        course_service.publish_course(course=published_course, actor=self.admin)

        self.client.force_authenticate(self.reviewer)

        response = self.client.get("/api/v1/review-queue/")
        ids = {r["id"] for r in response.data["data"]["results"]}
        self.assertEqual(
            ids,
            {
                str(submitted_course.id),
                str(approved_course.id),
                str(published_course.id),
            },
        )

        response = self.client.get("/api/v1/review-queue/", {"status": "APPROVED"})
        ids = {r["id"] for r in response.data["data"]["results"]}
        self.assertEqual(ids, {str(approved_course.id)})

        response = self.client.get("/api/v1/review-queue/", {"status": "PUBLISHED"})
        ids = {r["id"] for r in response.data["data"]["results"]}
        self.assertEqual(ids, {str(published_course.id)})

    def test_stored_newest_first_preference_applies_with_no_ordering_param(self):
        course1 = self._submitted_course()
        course2 = self._submitted_course()
        queue_preference_service.update_preference(
            user=self.reviewer, default_sort_order="NEWEST_FIRST"
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.get("/api/v1/review-queue/")
        ids = [r["id"] for r in response.data["data"]["results"]]
        self.assertEqual(ids, [str(course2.id), str(course1.id)])

    def test_explicit_ordering_param_overrides_stored_preference(self):
        course1 = self._submitted_course()
        course2 = self._submitted_course()
        queue_preference_service.update_preference(
            user=self.reviewer, default_sort_order="NEWEST_FIRST"
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.get(
            "/api/v1/review-queue/", {"ordering": "submitted_at"}
        )
        ids = [r["id"] for r in response.data["data"]["results"]]
        self.assertEqual(ids, [str(course1.id), str(course2.id)])

    def test_stored_track_filter_excludes_other_track(self):
        creator_category = make_category(
            track_preference=TrackPreference.CREATOR_PREFERRED
        )
        ai_category = make_category(track_preference=TrackPreference.AI_PREFERRED)
        creator_course = self._submitted_course(category=creator_category)
        self._submitted_course(category=ai_category)
        queue_preference_service.update_preference(
            user=self.reviewer,
            show_both_track=False,
            show_creator_track=True,
            show_ai_track=False,
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.get("/api/v1/review-queue/")
        ids = {r["id"] for r in response.data["data"]["results"]}
        self.assertEqual(ids, {str(creator_course.id)})

    def test_explicit_track_param_overrides_stored_preference(self):
        creator_category = make_category(
            track_preference=TrackPreference.CREATOR_PREFERRED
        )
        ai_category = make_category(track_preference=TrackPreference.AI_PREFERRED)
        self._submitted_course(category=creator_category)
        ai_course = self._submitted_course(category=ai_category)
        queue_preference_service.update_preference(
            user=self.reviewer,
            show_both_track=False,
            show_creator_track=True,
            show_ai_track=False,
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.get("/api/v1/review-queue/", {"track": "AI_TRACK"})
        ids = {r["id"] for r in response.data["data"]["results"]}
        self.assertEqual(ids, {str(ai_course.id)})

    def test_claim_transitions_to_in_review(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(f"/api/v1/review-queue/{course.id}/claim/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.IN_REVIEW)

    def test_content_approval_moves_course_to_qa_without_wallet_credit(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.QA_VERIFICATION)
        self.assertTrue(
            ReviewAction.objects.filter(course=course, action="APPROVE").exists()
        )

        wallet = wallet_service.get_or_create_wallet(user=self.creator)
        self.assertEqual(wallet.balance, Decimal("0.00"))

    def test_qa_approval_credits_wallet_after_required_media_is_registered(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)
        self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )

        self.client.force_authenticate(self.creator)
        for module in course.modules.all():
            for lesson in module.lessons.all():
                response = self.client.post(
                    f"/api/v1/courses/{course.id}/media-assets/",
                    {
                        "lesson": str(lesson.id),
                        "kind": "VIDEO",
                        "url": f"https://example.com/{lesson.id}.mp4",
                        "mime_type": "video/mp4",
                        "duration_seconds": 300,
                        "resolution": "1280x720",
                        "subtitle_url": f"https://example.com/{lesson.id}.srt",
                        "caption_accuracy_percent": "99.00",
                        "audio_lufs": "-16.00",
                        "audio_video_drift_ms": 50,
                        "accessibility": {"captions": True},
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        for kind, url in (
            ("PREVIEW_VIDEO", "https://example.com/preview.mp4"),
            ("THUMBNAIL", "https://example.com/thumb.jpg"),
        ):
            response = self.client.post(
                f"/api/v1/courses/{course.id}/media-assets/",
                {
                    "kind": kind,
                    "url": url,
                    "mime_type": "video/mp4"
                    if kind == "PREVIEW_VIDEO"
                    else "image/jpeg",
                    "duration_seconds": 60 if kind == "PREVIEW_VIDEO" else None,
                    "resolution": "1280x720",
                    "subtitle_url": "https://example.com/preview.srt"
                    if kind == "PREVIEW_VIDEO"
                    else "",
                    "caption_accuracy_percent": "99.00"
                    if kind == "PREVIEW_VIDEO"
                    else None,
                    "audio_lufs": "-16.00" if kind == "PREVIEW_VIDEO" else None,
                    "audio_video_drift_ms": 50 if kind == "PREVIEW_VIDEO" else None,
                    "accessibility": {"alt_text": "Course thumbnail"},
                },
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(self.qa_reviewer)
        self.assertEqual(
            self.client.post(f"/api/v1/review-queue/{course.id}/qa-claim/").status_code,
            status.HTTP_200_OK,
        )
        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/qa-approve/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.APPROVED)
        self.assertEqual(
            wallet_service.get_or_create_wallet(user=self.creator).balance,
            Decimal("120.00"),
        )

    def test_double_approve_returns_400(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reject_requires_summary(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/reject/", {"feedback": {}}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reject_reverts_course_to_draft(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/reject/",
            {"feedback": {"summary": "Needs more detail"}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.DRAFT)
        self.assertIsNotNone(course.rejected_at)
        self.assertTrue(
            ReviewAction.objects.filter(course=course, action="REJECT").exists()
        )

    def test_creator_cannot_review_own_course(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_reviewer_non_admin_forbidden(self):
        self._submitted_course()
        other = make_user(role=UserRole.COURSE_CREATOR)
        self.client.force_authenticate(other)

        response = self.client.get("/api/v1/review-queue/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_approve_without_reviewer_role(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unavailable_reviewer_cannot_claim(self):
        course = self._submitted_course()
        reviewer_availability_service.update_availability(
            user=self.reviewer, is_available=False
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(f"/api/v1/review-queue/{course.id}/claim/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unavailable_reviewer_cannot_approve(self):
        course = self._submitted_course()
        reviewer_availability_service.update_availability(
            user=self.reviewer, is_available=False
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unavailable_reviewer_cannot_reject(self):
        course = self._submitted_course()
        reviewer_availability_service.update_availability(
            user=self.reviewer, is_available=False
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/reject/",
            {"feedback": {"summary": "x"}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_idempotent_claim_still_works_after_going_unavailable(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)
        self.client.post(f"/api/v1/review-queue/{course.id}/claim/")

        reviewer_availability_service.update_availability(
            user=self.reviewer, is_available=False
        )
        response = self.client.post(f"/api/v1/review-queue/{course.id}/claim/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_claim_approve_reject_log_activity(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        self.client.post(f"/api/v1/review-queue/{course.id}/claim/")
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.reviewer, action="COURSE_ASSIGNED", category="COURSE"
            ).exists()
        )

        self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.reviewer, action="COURSE_APPROVED", category="APPROVAL"
            ).exists()
        )

        course2 = self._submitted_course()
        self.client.post(
            f"/api/v1/review-queue/{course2.id}/reject/",
            {"feedback": {"summary": "Needs work"}},
            format="json",
        )
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.reviewer, action="COURSE_REJECTED", category="APPROVAL"
            ).exists()
        )

    def test_submit_logs_activity_for_creator(self):
        self._submitted_course()

        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.creator, action="COURSE_SUBMITTED", category="SUBMISSION"
            ).exists()
        )

    def test_publish_logs_activity_for_actor(self):
        course = self._submitted_course()
        review_service.approve_course(course=course, reviewer=self.reviewer)
        self.client.force_authenticate(self.admin)

        self.client.post(f"/api/v1/courses/{course.id}/publish/")

        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.admin, action="COURSE_PUBLISHED", category="PUBLISH"
            ).exists()
        )


class ReviewerCourseScreenApiTests(APITestCase):
    """HTTP contract for the four Figma reviewer course screens."""

    def setUp(self):
        self.creator = make_user(
            role=UserRole.COURSE_CREATOR,
            email=f"figma-creator-{uuid4()}@example.com",
            first_name="Ada",
            last_name="Creator",
        )
        self.reviewer = make_user(
            role=UserRole.CREATOR_REVIEWER,
            email=f"figma-reviewer-{uuid4()}@example.com",
            first_name="Rita",
            last_name="Verifier",
        )
        self.other_reviewer = make_user(
            role=UserRole.CREATOR_REVIEWER,
            email=f"figma-other-reviewer-{uuid4()}@example.com",
        )
        self.category = make_category(
            name=f"Figma Category {uuid4()}",
            creator_price_beginner=Decimal("120.00"),
            creator_price_intermediate=Decimal("120.00"),
            creator_price_advanced=Decimal("120.00"),
        )
        CourseVersion.objects.get_or_create(label="1.0")

    def _course(
        self, *, status_value, title, source_type=CourseSourceType.CREATOR_UPLOADED
    ):
        course = build_compliant_course(creator=self.creator, category=self.category)
        now = timezone.now()
        course.title = title
        course.status = status_value
        course.source_type = source_type
        course.difficulty_level = "INTERMEDIATE"
        course.creator_price_snapshot = Decimal("120.00")
        course.submitted_at = now
        if status_value in {CourseStatus.APPROVED, CourseStatus.PUBLISHED}:
            course.approved_at = now
        if status_value == CourseStatus.PUBLISHED:
            course.published_at = now
        course.save()
        return course

    def _assign(self, course):
        return ReviewAssignment.objects.create(
            course=course,
            stage=ReviewStage.CONTENT,
            reviewer=self.reviewer,
            claimed_at=timezone.now(),
        )

    def _approve(self, course, *, reviewer=None, note="Ready to publish"):
        return ReviewAction.objects.create(
            course=course,
            reviewer=reviewer or self.reviewer,
            action=ReviewActionType.APPROVE,
            stage=ReviewStage.CONTENT,
            feedback={"summary": note},
        )

    def test_each_screen_enforces_its_status_and_returns_figma_row_fields(self):
        pending = self._course(status_value=CourseStatus.SUBMITTED, title="Pending")
        in_review = self._course(status_value=CourseStatus.IN_REVIEW, title="In review")
        approved = self._course(status_value=CourseStatus.APPROVED, title="Approved")
        published = self._course(status_value=CourseStatus.PUBLISHED, title="Published")
        self._assign(in_review)
        self._approve(approved)
        self._approve(published)
        CourseDistribution.objects.create(
            course=published,
            channel="SOLUDESK",
            learner_price=Decimal("250.98"),
            status=DistributionStatus.PUBLISHED,
        )
        self.client.force_authenticate(self.reviewer)

        expected = {
            "/api/v1/review-queue/pending/": pending,
            "/api/v1/review-queue/in-review/": in_review,
            "/api/v1/review-queue/approved/": approved,
            "/api/v1/review-queue/published/": published,
        }
        figma_fields = {
            "creator",
            "course_title",
            "course_id",
            "category",
            "difficulty_level",
            "reviewer",
            "reviewer_id",
            "approved_by",
            "date_reviewed",
            "last_reviewed_at",
            "reviewer_note",
            "price",
            "channels",
            "channel_summary",
            "source_label",
            "date_created",
        }
        for url, course in expected.items():
            with self.subTest(url=url):
                response = self.client.get(url, {"page": 1, "size": 50})
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertTrue(response.data["status"])
                self.assertIn("paginator", response.data["data"])
                rows = response.data["data"]["results"]
                self.assertEqual([row["course_id"] for row in rows], [str(course.id)])
                self.assertTrue(figma_fields.issubset(rows[0]))

    def test_pending_filters_match_figma_inputs_and_source_tabs(self):
        matching = self._course(
            status_value=CourseStatus.SUBMITTED,
            title="Machine Learning and Design",
            source_type=CourseSourceType.AI_GENERATED,
        )
        self._course(
            status_value=CourseStatus.SUBMITTED,
            title="Different creator course",
        )
        queue_preference_service.get_or_create_preference(user=self.reviewer)
        self.client.force_authenticate(self.reviewer)
        today = timezone.localdate().isoformat()

        response = self.client.get(
            "/api/v1/review-queue/pending/",
            {
                "search": "Machine Learning",
                "category": str(self.category.id),
                "difficulty_level": "INTERMEDIATE",
                "source_type": "AI_GENERATED",
                "date_from": today,
                "date_to": today,
                "ordering": "submitted_at",
                "page": 1,
                "size": 50,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["data"]["results"]
        self.assertEqual([row["course_id"] for row in rows], [str(matching.id)])
        self.assertEqual(rows[0]["source_label"], "AI Created")

        by_id = self.client.get(
            "/api/v1/review-queue/pending/", {"search": str(matching.id)}
        )
        self.assertEqual(by_id.status_code, status.HTTP_200_OK)
        self.assertEqual(by_id.data["data"]["paginator"]["count"], 1)

    def test_reviewer_and_approved_by_filters_match_review_history(self):
        in_review = self._course(
            status_value=CourseStatus.IN_REVIEW, title="Assigned to Rita"
        )
        approved = self._course(
            status_value=CourseStatus.APPROVED, title="Approved by Rita"
        )
        published = self._course(
            status_value=CourseStatus.PUBLISHED, title="Published by Rita"
        )
        self._assign(in_review)
        self._approve(approved)
        self._approve(published)
        self.client.force_authenticate(self.reviewer)

        cases = (
            ("/api/v1/review-queue/in-review/", "reviewer", in_review),
            ("/api/v1/review-queue/approved/", "reviewer", approved),
            ("/api/v1/review-queue/published/", "approved_by", published),
        )
        for url, parameter, expected in cases:
            with self.subTest(url=url):
                response = self.client.get(url, {parameter: str(self.reviewer.id)})
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                rows = response.data["data"]["results"]
                self.assertEqual([row["course_id"] for row in rows], [str(expected.id)])

                refused = self.client.get(url, {parameter: str(self.other_reviewer.id)})
                self.assertEqual(refused.data["data"]["paginator"]["count"], 0)

    def test_course_detail_contains_each_figma_drawer_block(self):
        course = self._course(
            status_value=CourseStatus.PUBLISHED,
            title="Machine Learning and Design",
            source_type=CourseSourceType.AI_GENERATED,
        )
        action = self._approve(course, note="Extend the lesson script")
        CourseDistribution.objects.create(
            course=course,
            channel="SOLUDESK",
            learner_price=Decimal("250.98"),
            status=DistributionStatus.PUBLISHED,
        )
        CourseDistribution.objects.create(
            course=course,
            channel="COURSERA",
            learner_price=Decimal("250.00"),
            status=DistributionStatus.QUEUED,
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.get(f"/api/v1/review-queue/{course.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["course_id"], str(course.id))
        self.assertEqual(response.data["course_title"], course.title)
        self.assertEqual(response.data["source_label"], "AI Created")
        self.assertEqual(
            response.data["review_information"]["reviewer_id"],
            str(self.reviewer.id),
        )
        self.assertIsNotNone(action.created_datetime)
        self.assertIsNotNone(response.data["review_information"]["date_reviewed"])
        self.assertEqual(
            response.data["review_information"]["reviewer_note"],
            "Extend the lesson script",
        )
        self.assertEqual(
            response.data["owner_information"]["user_id"], str(self.creator.id)
        )
        self.assertEqual(
            [row["channel_label"] for row in response.data["price_information"]],
            ["SoluDesk", "Coursera Marketplace"],
        )
        self.assertEqual(response.data["channel_summary"], "SoluDesk & Coursera")

    def test_reviewer_can_save_prices_and_publish_from_approved_screen(self):
        course = self._course(status_value=CourseStatus.APPROVED, title="Publish me")
        self._approve(course)
        payload = {
            "distribution_channels": [
                {
                    "channel": "SOLUDESK",
                    "approval_rate": "Published within 60 seconds",
                    "learner_price": "149.00",
                    "mie_suggestion": "140.00",
                    "model": "ONE_TIME",
                    "platform_revenue_per_enrollment": "149.00",
                    "mie_explanation": "Suggested from competitor analysis.",
                    "comparable_courses": [
                        {
                            "course_title": "Modern computing language",
                            "difficulty_level": "BEGINNER",
                            "learner_price": "150.00",
                        }
                    ],
                },
                {
                    "channel": "UDEMY",
                    "approval_rate": "Published within 10 - 15 minutes",
                    "learner_price": "190.00",
                    "model": "ONE_TIME",
                    "course_fee_percent": "32.00",
                    "promotional_pricing": "150.00",
                },
            ]
        }
        self.client.force_authenticate(self.reviewer)

        save_response = self.client.put(
            f"/api/v1/review-queue/{course.id}/review-prices/",
            payload,
            format="json",
        )
        self.assertEqual(save_response.status_code, status.HTTP_200_OK)
        self.assertEqual(save_response.data[0]["channel_label"], "SoluDesk")
        self.assertEqual(save_response.data[0]["creator_payout_fixed"], "120.00")
        self.assertEqual(save_response.data[0]["mie_suggestion"], "140.00")

        get_response = self.client.get(
            f"/api/v1/review-queue/{course.id}/review-prices/"
        )
        self.assertEqual(get_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(get_response.data), 2)

        publish_response = self.client.post(
            f"/api/v1/review-queue/{course.id}/publish/", {}, format="json"
        )
        self.assertEqual(publish_response.status_code, status.HTTP_200_OK)
        self.assertEqual(publish_response.data["status"], CourseStatus.PUBLISHED)
        self.assertEqual(publish_response.data["channels"], ["SOLUDESK", "UDEMY"])
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.PUBLISHED)
        self.assertEqual(
            CourseDistribution.objects.get(course=course, channel="SOLUDESK").status,
            DistributionStatus.PUBLISHED,
        )
        self.assertEqual(
            CourseDistribution.objects.get(course=course, channel="UDEMY").status,
            DistributionStatus.QUEUED,
        )

    def test_screen_and_publish_permissions_and_invalid_states(self):
        approved = self._course(status_value=CourseStatus.APPROVED, title="Approved")
        draft = self._course(status_value=CourseStatus.DRAFT, title="Draft")

        self.assertEqual(
            self.client.get("/api/v1/review-queue/pending/").status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        self.client.force_authenticate(self.creator)
        self.assertEqual(
            self.client.get("/api/v1/review-queue/approved/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(
                f"/api/v1/review-queue/{approved.id}/publish/", {}, format="json"
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.client.force_authenticate(self.reviewer)
        self.assertEqual(
            self.client.get(
                f"/api/v1/review-queue/{draft.id}/review-prices/"
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.client.post(
                f"/api/v1/review-queue/{draft.id}/publish/", {}, format="json"
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/review-queue/00000000-0000-0000-0000-000000000000/"
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_published_list_query_count_does_not_grow_per_row(self):
        queue_preference_service.get_or_create_preference(user=self.reviewer)
        first = self._course(status_value=CourseStatus.PUBLISHED, title="Published 1")
        self._approve(first)
        CourseDistribution.objects.create(
            course=first,
            channel="SOLUDESK",
            learner_price=Decimal("149.00"),
        )
        self.client.force_authenticate(self.reviewer)

        with CaptureQueriesContext(connection) as one_row_queries:
            response = self.client.get("/api/v1/review-queue/published/", {"size": 50})
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        for number in range(2, 6):
            course = self._course(
                status_value=CourseStatus.PUBLISHED,
                title=f"Published {number}",
            )
            self._approve(course)
            CourseDistribution.objects.create(
                course=course,
                channel="SOLUDESK",
                learner_price=Decimal("149.00"),
            )

        with CaptureQueriesContext(connection) as five_row_queries:
            response = self.client.get("/api/v1/review-queue/published/", {"size": 50})
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(one_row_queries), len(five_row_queries))


class ReviewServiceRoleEnforcementTests(APITestCase):
    """Direct service-level calls, bypassing the view layer entirely - the
    defense-in-depth these should still block even if a caller other than
    CourseReviewViewSet reached review_service directly."""

    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.category = make_category(
            creator_price_beginner=Decimal("120.00"),
            creator_price_intermediate=Decimal("120.00"),
            creator_price_advanced=Decimal("120.00"),
        )

    def _submitted_course(self):
        course = build_compliant_course(creator=self.creator, category=self.category)
        return course_service.submit_course(course=course, actor=self.creator)

    def test_wrong_role_cannot_approve(self):
        course = self._submitted_course()
        wrong_role_reviewer = make_user(role=UserRole.COURSE_CREATOR)

        with self.assertRaises(PermissionDenied):
            review_service.approve_course(course=course, reviewer=wrong_role_reviewer)

    def test_wrong_role_cannot_reject(self):
        course = self._submitted_course()
        wrong_role_reviewer = make_user(role=UserRole.COURSE_CREATOR)

        with self.assertRaises(PermissionDenied):
            review_service.reject_course(
                course=course,
                reviewer=wrong_role_reviewer,
                feedback={"summary": "Needs work."},
            )
