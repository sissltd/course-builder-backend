from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.services import course_service, review_service
from api.courses.models import Topic
from api.courses.tests.factories import (
    build_compliant_course,
    make_category,
    make_topic,
    make_user,
)
from api.wallet.services import wallet_service
from api.users.enums import UserRole


class TopicApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.category = make_category(name="Software Engineering")

    def test_list_requires_authentication(self):
        response = self.client.get("/api/v1/topics/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_can_list_topics(self):
        make_topic(category=self.category, name="Frontend Development")
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/topics/")
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
                "creator_price": "25.00",
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
                "creator_price": "25.00",
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
                "creator_price": "25.00",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_reviewer_can_update_price(self):
        topic = make_topic(category=self.category, name="Frontend Development")
        self.client.force_authenticate(self.reviewer)

        response = self.client.patch(
            f"/api/v1/topics/{topic.id}/", {"creator_price": "30.00"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_reviewer_price_update_refreshes_submitted_course_queue_snapshot(self):
        topic = make_topic(
            category=self.category,
            name="Frontend Development",
            creator_price=Decimal("25.00"),
        )
        course = build_compliant_course(creator=self.creator, category=self.category)
        course.topic = topic
        course.save(update_fields=["topic"])
        course_service.submit_course(course=course, actor=self.creator)

        self.client.force_authenticate(self.reviewer)
        response = self.client.patch(
            f"/api/v1/topics/{topic.id}/", {"creator_price": "30.00"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        queue_response = self.client.get("/api/v1/review-queue/")
        queue_course = queue_response.data["data"]["results"][0]
        self.assertEqual(queue_course["creator_price_snapshot"], "30.00")

        course.refresh_from_db()
        self.assertEqual(course.creator_price_snapshot, Decimal("30.00"))

        # Use the service directly: the /approve/ endpoint now calls
        # approve_content (→ QA) which does not credit the wallet.
        # approve_course is the full-approval path that credits the wallet.
        review_service.approve_course(course=course, reviewer=self.reviewer)
        wallet = wallet_service.get_or_create_wallet(user=self.creator)
        self.assertEqual(wallet.balance, Decimal("30.00"))

    def test_admin_and_superadmin_can_refresh_submitted_course_queue_snapshot(self):
        for role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
            with self.subTest(role=role):
                actor = make_user(role=role)
                topic = make_topic(
                    category=self.category,
                    creator_price=Decimal("25.00"),
                )
                course = build_compliant_course(
                    creator=self.creator,
                    category=self.category,
                )
                course.topic = topic
                course.save(update_fields=["topic"])
                course_service.submit_course(course=course, actor=self.creator)

                self.client.force_authenticate(actor)
                response = self.client.patch(
                    f"/api/v1/topics/{topic.id}/",
                    {"creator_price": "35.00"},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                course.refresh_from_db()
                self.assertEqual(course.creator_price_snapshot, Decimal("35.00"))

    def test_reviewer_price_update_does_not_refresh_approved_course_snapshot(self):
        topic = make_topic(
            category=self.category,
            name="Backend Development",
            creator_price=Decimal("25.00"),
        )
        course = build_compliant_course(creator=self.creator, category=self.category)
        course.topic = topic
        course.save(update_fields=["topic"])
        course_service.submit_course(course=course, actor=self.creator)
        review_service.approve_course(course=course, reviewer=self.reviewer)

        self.client.force_authenticate(self.reviewer)
        response = self.client.patch(
            f"/api/v1/topics/{topic.id}/", {"creator_price": "30.00"}, format="json"
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
                "creator_price": "25.00",
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
                "creator_price": "25.00",
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
