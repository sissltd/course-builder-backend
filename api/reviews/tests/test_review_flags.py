from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.services import course_service
from api.courses.tests.factories import build_compliant_course, make_user
from api.reviews.models import ReviewFlag
from api.reviews.services import review_service
from api.users.enums import UserRole


class ReviewFlagApiTests(APITestCase):
    """Structured review flags ride along with a rejection."""

    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)

    def _submitted_course(self):
        course = build_compliant_course(creator=self.creator)
        return course_service.submit_course(course=course, actor=self.creator)

    def test_reject_with_flags_persists_structured_issues(self):
        course = self._submitted_course()
        module = course.modules.first()
        lesson = module.lessons.first()

        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/reject/",
            {
                "feedback": {"summary": "Needs script work."},
                "flags": [
                    {
                        "flag_type": "script_length",
                        "title": "P1 Lesson 2 - Script Length",
                        "system_message": "306/500 words below minimum",
                        "reviewer_note": "Extend the lesson script to resolve this issue.",
                        "lesson_id": str(lesson.id),
                        "module_id": str(module.id),
                    },
                    {
                        "flag_type": "missing_media",
                        "title": "No preview video",
                        "system_message": "",
                        "reviewer_note": "Add a 1-2 minute overview video.",
                    },
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        flags = ReviewFlag.objects.filter(review_action__course=course)
        self.assertEqual(flags.count(), 2)
        script_flag = flags.get(flag_type="script_length")
        self.assertEqual(script_flag.lesson_id, lesson.id)
        self.assertEqual(script_flag.module_id, module.id)
        self.assertFalse(script_flag.is_resolved)
        # Flags are nested in the response for the creator dashboard.
        self.assertEqual(len(response.data["flags"]), 2)

    def test_reject_without_flags_still_works(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/reject/",
            {"feedback": {"summary": "Not ready."}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            ReviewFlag.objects.filter(review_action__course=course).count(), 0
        )

    def test_flag_missing_title_rejected(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/reject/",
            {
                "feedback": {"summary": "Needs work."},
                "flags": [{"flag_type": "script_length"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_flag_from_other_course_lesson_rejected(self):
        course = self._submitted_course()
        other = self._submitted_course()
        foreign_lesson = other.modules.first().lessons.first()

        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/reject/",
            {
                "feedback": {"summary": "Needs work."},
                "flags": [
                    {
                        "flag_type": "script_length",
                        "title": "Bad scope",
                        "lesson_id": str(foreign_lesson.id),
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # The whole rejection rolled back - course still awaiting review.
        course.refresh_from_db()
        self.assertEqual(course.status, "SUBMITTED")

    def test_service_level_flags_via_review_service(self):
        course = self._submitted_course()
        review_action = review_service.reject_course(
            course=course,
            reviewer=self.reviewer,
            feedback={"summary": "Service-level rejection."},
            flags=[
                {
                    "flag_type": "quiz_incomplete",
                    "title": "Final quiz too short",
                    "system_message": "3/10 questions",
                }
            ],
        )
        self.assertEqual(review_action.flags.count(), 1)
        self.assertEqual(
            review_action.flags.first().title, "Final quiz too short"
        )
