from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.tests.factories import build_compliant_course, make_user
from api.reviews.models import CourseQualityCheck, QualityCheckCriterion
from api.users.enums import UserRole


class QualityCheckTemplateApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)

    def test_seeded_criteria_exist(self):
        self.assertGreaterEqual(QualityCheckCriterion.objects.count(), 12)

    def test_creator_lists_only_active_criteria(self):
        retired = QualityCheckCriterion.objects.create(
            section="Course information", label="Retired item", is_active=False
        )
        self.client.force_authenticate(self.creator)
        response = self.client.get("/api/v1/quality-check-criteria/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        labels = [c["label"] for c in response.data]
        self.assertIn("Course title", labels)
        self.assertNotIn(retired.label, labels)

    def test_admin_sees_retired_criteria_too(self):
        QualityCheckCriterion.objects.create(
            section="Course information", label="Retired item", is_active=False
        )
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/quality-check-criteria/")
        labels = [c["label"] for c in response.data]
        self.assertIn("Retired item", labels)

    def test_creator_cannot_add_criteria(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            "/api/v1/quality-check-criteria/",
            {"section": "New", "label": "X", "order_index": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_add_criteria(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/v1/quality-check-criteria/",
            {"section": "New", "label": "Custom item", "order_index": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class CourseQualityCheckApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)

    def test_refresh_marks_automated_criteria(self):
        course = build_compliant_course(creator=self.creator)

        self.client.force_authenticate(self.creator)
        response = self.client.post(f"/api/v1/courses/{course.id}/quality-checks/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.data
        self.assertGreaterEqual(len(results), 10)
        by_label = {r["criterion"]["label"]: r for r in results}
        # A compliant course passes every automated criterion.
        for label in (
            "Course description",
            "Learning objectives",
            "Preview video",
            "Module count",
            "Lessons per module",
            "Lesson scripts",
            "Final assessment",
            "Version selected",
        ):
            self.assertTrue(
                by_label[label]["is_checked"], msg=f"{label} should pass"
            )
            self.assertEqual(by_label[label]["warning_note"], "")

    def test_refresh_flags_failures_with_warning_notes(self):
        course = build_compliant_course(creator=self.creator)
        # Break one criterion: shorten the description below the minimum.
        course.description = "too short"
        course.save(update_fields=["description"])

        self.client.force_authenticate(self.creator)
        response = self.client.post(f"/api/v1/courses/{course.id}/quality-checks/")
        by_label = {r["criterion"]["label"]: r for r in response.data}
        self.assertFalse(by_label["Course description"]["is_checked"])
        self.assertIn("description", by_label["Course description"]["warning_note"])

    def test_get_returns_upserted_results(self):
        course = build_compliant_course(creator=self.creator)
        self.client.force_authenticate(self.creator)
        self.client.post(f"/api/v1/courses/{course.id}/quality-checks/")
        response = self.client.get(f"/api/v1/courses/{course.id}/quality-checks/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 10)
        # One row per criterion, not per refresh.
        self.assertEqual(
            CourseQualityCheck.objects.filter(course=course).count(),
            QualityCheckCriterion.objects.filter(is_active=True).count(),
        )

    def test_outsider_gets_404(self):
        course = build_compliant_course(creator=self.creator)
        outsider = make_user()
        self.client.force_authenticate(outsider)
        response = self.client.get(f"/api/v1/courses/{course.id}/quality-checks/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
