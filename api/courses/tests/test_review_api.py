from decimal import Decimal

from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APITestCase

from api.categories.enums import TrackPreference
from api.courses.enums import CourseStatus
from api.courses.models import ReviewAction
from api.courses.services import course_service, review_service
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
        self.category = make_category(creator_price=Decimal("120.00"))

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
            user=self.reviewer, track_filter="CREATOR_TRACK"
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
            user=self.reviewer, track_filter="CREATOR_TRACK"
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


class ReviewServiceRoleEnforcementTests(APITestCase):
    """Direct service-level calls, bypassing the view layer entirely - the
    defense-in-depth these should still block even if a caller other than
    CourseReviewViewSet reached review_service directly."""

    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.category = make_category(creator_price=Decimal("120.00"))

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
