"""The creator's own achievements, and badges on their profile."""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.achievements.enums import BadgeCriterion
from api.achievements.tests.factories import make_award, make_badge
from api.courses.tests.factories import make_draft_course, make_user
from api.users.enums import UserRole

URL = "/api/v1/creator/achievements/"


class CreatorAchievementTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.client.force_authenticate(self.creator)

    def test_progress_per_badge(self):
        make_draft_course(creator=self.creator)
        make_draft_course(
            creator=self.creator,
            published_at=timezone.now(),
            approved_at=timezone.now(),
        )
        created = make_badge(criterion=BadgeCriterion.COURSES_CREATED, required_count=2)
        published = make_badge(
            criterion=BadgeCriterion.COURSES_PUBLISHED, required_count=5
        )
        make_award(badge=created, creator=self.creator)
        make_badge(title="Gone", is_deleted=True)

        response = self.client.get(URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = {row["badge"]["id"]: row for row in response.data["data"]}
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[str(created.id)]["earned"])
        self.assertIsNotNone(rows[str(created.id)]["awarded_at"])
        self.assertEqual(rows[str(created.id)]["current_count"], 2)
        self.assertFalse(rows[str(published.id)]["earned"])
        self.assertIsNone(rows[str(published.id)]["awarded_at"])
        self.assertEqual(rows[str(published.id)]["current_count"], 1)
        self.assertEqual(
            rows[str(published.id)]["badge"]["requirement_summary"],
            "For creators who have published 5 courses",
        )

    def test_writer_can_read_theirs(self):
        self.client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))

        self.assertEqual(self.client.get(URL).status_code, status.HTTP_200_OK)

    def test_non_authoring_roles_are_refused(self):
        for role in (UserRole.CREATOR_REVIEWER, UserRole.ADMIN, UserRole.QA_REVIEWER):
            with self.subTest(role=role):
                self.client.force_authenticate(make_user(role=role))
                self.assertEqual(
                    self.client.get(URL).status_code, status.HTTP_403_FORBIDDEN
                )

    def test_unauthenticated_is_refused(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.client.get(URL).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_query_count_does_not_grow_with_badges(self):
        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(URL)
            return len(ctx.captured_queries)

        make_award(badge=make_badge(), creator=self.creator)
        one = count_queries()
        for _ in range(4):
            make_award(badge=make_badge(), creator=self.creator)
        self.assertEqual(count_queries(), one)

    def test_profile_lists_held_badges_and_role_label(self):
        badge = make_badge(title="Top", icon="diamond", color="#F2994A")
        make_award(badge=badge, creator=self.creator)
        make_award(
            badge=make_badge(title="Revoked"), creator=self.creator, is_deleted=True
        )

        response = self.client.get("/api/v1/users/me/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["role_label"], "Course Creator")
        self.assertEqual(
            [
                {key: entry[key] for key in ("code", "label", "icon", "color")}
                for entry in response.data["badges"]
            ],
            [
                {
                    "code": str(badge.id),
                    "label": "Top",
                    "icon": "diamond",
                    "color": "#F2994A",
                }
            ],
        )
