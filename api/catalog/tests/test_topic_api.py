from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.services import course_service
from api.catalog.models import Topic
from api.courses.tests.factories import (
    build_compliant_course,
    make_category,
    make_topic,
    make_user,
)
from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.users.enums import UserRole


class TopicApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.writer = make_user(role=UserRole.STAFF_WRITER)
        self.category = make_category(name="Software Engineering")

    def test_list_requires_authentication(self):
        response = self.client.get("/api/v1/topics/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_can_list_topics(self):
        make_topic(category=self.category, name="Frontend Development")
        self.client.force_authenticate(self.creator)

        response = self.client.get(
            "/api/v1/topics/", {"category": str(self.category.id)}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]["results"]), 1)

    def test_filter_by_category(self):
        other_category = make_category(name="Design")
        make_topic(category=self.category, name="Frontend Development")
        make_topic(category=other_category, name="UX Research")
        self.client.force_authenticate(self.creator)

        response = self.client.get(
            "/api/v1/topics/", {"category": str(self.category.id)}
        )
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Frontend Development")

    def test_creator_cannot_create_topic(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/topics/",
            {
                "category": str(self.category.id),
                "name": "Rust Development",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_create_topic(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/topics/",
            {
                "category": str(self.category.id),
                "name": "Rust Development",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Topic.objects.filter(name="Rust Development").exists())

    def test_reviewer_can_create_topic(self):
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            "/api/v1/topics/",
            {
                "category": str(self.category.id),
                "name": "Rust Development",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_admin_can_create_a_topic_with_its_own_price(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/topics/",
            {
                "category": str(self.category.id),
                "name": "Rust Development",
                "creator_price_with_video": "250.00",
                "status": "ACTIVE",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["creator_price_with_video"], "250.00")
        self.assertIsNone(response.data["creator_price_ai_built"])

    def test_a_reviewer_may_create_a_topic_but_not_give_it_a_price(self):
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            "/api/v1/topics/",
            {
                "category": str(self.category.id),
                "name": "Rust Development",
                "creator_price_with_video": "250.00",
                "status": "ACTIVE",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Topic.objects.filter(name="Rust Development").exists())

    def test_a_reviewer_may_edit_a_topic_but_not_reprice_it(self):
        topic = make_topic(category=self.category, name="Frontend Development")
        self.client.force_authenticate(self.reviewer)

        renamed = self.client.patch(f"/api/v1/topics/{topic.id}/", {"name": "Frontend"}, format="json")
        repriced = self.client.patch(
            f"/api/v1/topics/{topic.id}/", {"creator_price_with_video": "30.00"}, format="json"
        )

        self.assertEqual(renamed.status_code, status.HTTP_200_OK)
        self.assertEqual(repriced.status_code, status.HTTP_403_FORBIDDEN)
        topic.refresh_from_db()
        self.assertEqual(topic.creator_price_with_video, Decimal("25.00"))

    def test_the_writer_may_reprice_a_topic_but_not_edit_the_rest(self):
        topic = make_topic(category=self.category, name="Frontend Development")
        self.client.force_authenticate(self.writer)

        repriced = self.client.patch(
            f"/api/v1/topics/{topic.id}/", {"creator_price_with_video": "30.00"}, format="json"
        )
        renamed = self.client.patch(f"/api/v1/topics/{topic.id}/", {"name": "Frontend"}, format="json")
        # Re-sending an unchanged name alongside a price is not an edit of it.
        both = self.client.patch(
            f"/api/v1/topics/{topic.id}/",
            {"name": "Frontend Development", "creator_price_with_video": "31.00"},
            format="json",
        )

        self.assertEqual(repriced.status_code, status.HTTP_200_OK)
        self.assertEqual(renamed.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(both.status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.delete(f"/api/v1/topics/{topic.id}/").status_code, status.HTTP_403_FORBIDDEN)

    def test_a_reprice_re_prices_unpaid_courses_in_the_review_queue(self):
        topic = make_topic(category=self.category, name="Frontend Development")
        course = build_compliant_course(creator=self.creator, category=self.category)
        course.topic = topic
        course.save(update_fields=["topic"])
        course_service.submit_course(course=course, actor=self.creator)

        self.client.force_authenticate(self.writer)
        response = self.client.patch(
            f"/api/v1/topics/{topic.id}/", {"creator_price_with_video": "30.00"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.creator_price_snapshot, Decimal("30.00"))
        self.assertEqual(course.creator_rates_snapshot["WITH_VIDEO"], "30.00")

    def test_admin_and_superadmin_can_reprice_queued_courses(self):
        for role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
            with self.subTest(role=role):
                actor = make_user(role=role)
                topic = make_topic(category=self.category)
                course = build_compliant_course(creator=self.creator, category=self.category)
                course.topic = topic
                course.save(update_fields=["topic"])
                course_service.submit_course(course=course, actor=self.creator)

                self.client.force_authenticate(actor)
                response = self.client.patch(
                    f"/api/v1/topics/{topic.id}/", {"creator_price_with_video": "35.00"}, format="json"
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                course.refresh_from_db()
                self.assertEqual(course.creator_price_snapshot, Decimal("35.00"))

    def test_a_reprice_leaves_approved_and_paid_courses_alone(self):
        topic = make_topic(category=self.category, name="Backend Development")
        course = build_compliant_course(creator=self.creator, category=self.category)
        course.topic = topic
        course.save(update_fields=["topic"])
        course_service.submit_course(course=course, actor=self.creator)
        Course.objects.filter(pk=course.pk).update(status=CourseStatus.APPROVED)

        self.client.force_authenticate(self.writer)
        response = self.client.patch(
            f"/api/v1/topics/{topic.id}/", {"creator_price_with_video": "30.00"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.creator_price_snapshot, Decimal("25.00"))

    def test_duplicate_name_within_category_rejected(self):
        make_topic(category=self.category, name="Frontend Development")
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/topics/",
            {
                "category": str(self.category.id),
                "name": "Frontend Development",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_same_name_in_different_category_allowed(self):
        other_category = make_category(name="Design")
        make_topic(category=self.category, name="Frontend Development")
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/topics/",
            {
                "category": str(other_category.id),
                "name": "Frontend Development",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_admin_can_delete_topic(self):
        topic = make_topic(category=self.category)
        self.client.force_authenticate(self.admin)

        response = self.client.delete(f"/api/v1/topics/{topic.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Topic.objects.filter(id=topic.id).exists())
