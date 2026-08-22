from decimal import Decimal

from django.test import TestCase

from api.courses.services import course_service
from api.courses.tests.factories import (
    build_compliant_course,
    make_category,
    make_topic,
    make_user,
)
from api.users.enums import UserRole


class TopicAdminTests(TestCase):
    """Editing a Topic via Django admin must obey the same rules as the API.

    Regression coverage for a bug where TopicAdmin's default save_model()
    bypassed topic_service.update_topic(), silently skipping the
    creator_price_snapshot refresh on courses still in the review queue.
    """

    def setUp(self):
        self.superadmin = make_user(
            role=UserRole.SUPER_ADMIN, is_staff=True, is_superuser=True
        )
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.category = make_category(name="Software Engineering")
        self.client.force_login(self.superadmin)

    def test_admin_price_edit_refreshes_submitted_course_queue_snapshot(self):
        topic = make_topic(
            category=self.category,
            name="Backend Development",
            creator_price=Decimal("25.00"),
        )
        course = build_compliant_course(creator=self.creator, category=self.category)
        course.topic = topic
        course.save(update_fields=["topic"])
        course_service.submit_course(course=course, actor=self.creator)

        response = self.client.post(
            f"/admin/catalog/topic/{topic.id}/change/",
            {
                "category": str(self.category.id),
                "name": topic.name,
                "creator_price": "40.00",
                "status": topic.status,
            },
        )
        self.assertEqual(response.status_code, 302)

        topic.refresh_from_db()
        self.assertEqual(topic.creator_price, Decimal("40.00"))
        self.assertEqual(topic.updated_by_id, self.superadmin.id)

        course.refresh_from_db()
        self.assertEqual(course.creator_price_snapshot, Decimal("40.00"))

    def test_admin_create_stamps_created_by_and_updated_by(self):
        response = self.client.post(
            "/admin/catalog/topic/add/",
            {
                "category": str(self.category.id),
                "name": "Data Engineering",
                "creator_price": "50.00",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, 302)

        from api.catalog.models import Topic

        topic = Topic.objects.get(name="Data Engineering")
        self.assertEqual(topic.created_by_id, self.superadmin.id)
        self.assertEqual(topic.updated_by_id, self.superadmin.id)
