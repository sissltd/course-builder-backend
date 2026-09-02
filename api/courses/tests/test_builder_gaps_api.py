"""Cover the builder endpoints added for the frontend integration gaps.

Two of these guard bugs that were live rather than hypothetical: the
module edit-lock actions existed but were never routed, and a naive
row-by-row reorder violates the non-deferrable unique constraints on
(course, order) / (module, order) the moment two adjacent items swap.
"""

from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.models import Lesson, Module
from api.courses.tests.factories import make_draft_course, make_user
from api.users.enums import UserRole


class ModuleLockRoutingTests(APITestCase):
    """The lock/unlock/heartbeat actions were implemented but unreachable
    because ModuleViewSet is mounted with explicit method maps."""

    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.other = make_user(role=UserRole.COURSE_CREATOR)
        self.course = make_draft_course(creator=self.creator)
        self.module = Module.objects.create(
            course=self.course, title="M1", order=1
        )
        self.base = (
            f"/api/v1/courses/{self.course.id}/modules/{self.module.id}"
        )

    def test_lock_route_reaches_the_service(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(f"{self.base}/lock/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_locked"])
        self.assertEqual(str(response.data["locked_by"]), str(self.creator.id))

    def test_second_holder_is_refused(self):
        self.client.force_authenticate(self.creator)
        self.client.post(f"{self.base}/lock/")

        # A second creator with access must not be able to steal the lock.
        from api.collaborators.enums import CollaboratorRole
        from api.collaborators.tests.factories import make_collaborator

        make_collaborator(
            course=self.course, user=self.other, role=CollaboratorRole.ADMIN
        )
        self.client.force_authenticate(self.other)

        response = self.client.post(f"{self.base}/lock/")

        self.assertEqual(response.status_code, status.HTTP_423_LOCKED)

    def test_unlock_and_heartbeat_are_routed(self):
        self.client.force_authenticate(self.creator)
        self.client.post(f"{self.base}/lock/")

        beat = self.client.post(f"{self.base}/heartbeat/")
        unlock = self.client.post(f"{self.base}/unlock/")

        self.assertEqual(beat.status_code, status.HTTP_200_OK)
        self.assertEqual(unlock.status_code, status.HTTP_200_OK)
        self.assertFalse(unlock.data["is_locked"])

    def test_heartbeat_without_a_lock_is_refused(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(f"{self.base}/heartbeat/")

        self.assertEqual(response.status_code, status.HTTP_423_LOCKED)


class ModuleReorderTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.course = make_draft_course(creator=self.creator)
        self.modules = [
            Module.objects.create(course=self.course, title=f"M{i}", order=i)
            for i in (1, 2, 3)
        ]
        self.url = f"/api/v1/courses/{self.course.id}/modules/reorder/"
        self.client.force_authenticate(self.creator)

    def _order(self):
        return list(
            Module.objects.filter(course=self.course)
            .order_by("order")
            .values_list("title", flat=True)
        )

    def test_adjacent_swap(self):
        """The case a naive row-by-row write cannot do: both rows briefly
        hold the same order, violating unique_module_order_per_course."""

        a, b, c = self.modules
        response = self.client.patch(
            self.url,
            {
                "order": [
                    {"id": str(b.id), "order": 1},
                    {"id": str(a.id), "order": 2},
                    {"id": str(c.id), "order": 3},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._order(), ["M2", "M1", "M3"])

    def test_full_reversal(self):
        a, b, c = self.modules
        response = self.client.patch(
            self.url,
            {
                "order": [
                    {"id": str(c.id), "order": 1},
                    {"id": str(b.id), "order": 2},
                    {"id": str(a.id), "order": 3},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._order(), ["M3", "M2", "M1"])
        self.assertEqual(
            [row["title"] for row in response.data], ["M3", "M2", "M1"]
        )

    def test_partial_list_is_rejected_without_writing(self):
        a, b, _c = self.modules

        response = self.client.patch(
            self.url,
            {"order": [{"id": str(b.id), "order": 1}, {"id": str(a.id), "order": 2}]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._order(), ["M1", "M2", "M3"])

    def test_foreign_id_is_rejected_without_writing(self):
        other_course = make_draft_course(creator=self.creator)
        foreign = Module.objects.create(
            course=other_course, title="Foreign", order=1
        )
        a, b, _c = self.modules

        response = self.client.patch(
            self.url,
            {
                "order": [
                    {"id": str(a.id), "order": 1},
                    {"id": str(b.id), "order": 2},
                    {"id": str(foreign.id), "order": 3},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._order(), ["M1", "M2", "M3"])

    def test_duplicate_order_values_are_rejected(self):
        a, b, c = self.modules

        response = self.client.patch(
            self.url,
            {
                "order": [
                    {"id": str(a.id), "order": 1},
                    {"id": str(b.id), "order": 1},
                    {"id": str(c.id), "order": 2},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_draft_course_is_refused(self):
        self.course.status = CourseStatus.SUBMITTED
        self.course.save(update_fields=["status"])
        a, b, c = self.modules

        response = self.client.patch(
            self.url,
            {
                "order": [
                    {"id": str(c.id), "order": 1},
                    {"id": str(b.id), "order": 2},
                    {"id": str(a.id), "order": 3},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_another_creator_cannot_reorder(self):
        outsider = make_user(role=UserRole.COURSE_CREATOR)
        self.client.force_authenticate(outsider)
        a, b, c = self.modules

        response = self.client.patch(
            self.url,
            {
                "order": [
                    {"id": str(c.id), "order": 1},
                    {"id": str(b.id), "order": 2},
                    {"id": str(a.id), "order": 3},
                ]
            },
            format="json",
        )

        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )
        self.assertEqual(self._order(), ["M1", "M2", "M3"])


class LessonReorderTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.course = make_draft_course(creator=self.creator)
        self.module = Module.objects.create(
            course=self.course, title="M1", order=1
        )
        self.lessons = [
            Lesson.objects.create(module=self.module, title=f"L{i}", order=i)
            for i in (1, 2, 3)
        ]
        self.url = (
            f"/api/v1/courses/{self.course.id}/modules/"
            f"{self.module.id}/lessons/reorder/"
        )
        self.client.force_authenticate(self.creator)

    def _order(self):
        return list(
            Lesson.objects.filter(module=self.module)
            .order_by("order")
            .values_list("title", flat=True)
        )

    def test_adjacent_swap(self):
        a, b, c = self.lessons

        response = self.client.patch(
            self.url,
            {
                "order": [
                    {"id": str(b.id), "order": 1},
                    {"id": str(a.id), "order": 2},
                    {"id": str(c.id), "order": 3},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._order(), ["L2", "L1", "L3"])

    def test_lesson_from_another_module_is_rejected(self):
        other_module = Module.objects.create(
            course=self.course, title="M2", order=2
        )
        foreign = Lesson.objects.create(
            module=other_module, title="Foreign", order=1
        )
        a, b, _c = self.lessons

        response = self.client.patch(
            self.url,
            {
                "order": [
                    {"id": str(a.id), "order": 1},
                    {"id": str(b.id), "order": 2},
                    {"id": str(foreign.id), "order": 3},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._order(), ["L1", "L2", "L3"])


class ContentBlockBulkTests(APITestCase):
    """The rich-text editor saves the whole lesson body in one request."""

    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.course = make_draft_course(creator=self.creator)
        self.module = Module.objects.create(
            course=self.course, title="M1", order=1
        )
        self.lesson = Lesson.objects.create(
            module=self.module, title="L1", order=1
        )
        self.url = (
            f"/api/v1/courses/{self.course.id}/modules/{self.module.id}"
            f"/lessons/{self.lesson.id}/content-blocks/bulk/"
        )
        self.client.force_authenticate(self.creator)

    def _blocks(self):
        from api.courses.models import LessonContentBlock

        return list(
            LessonContentBlock.objects.filter(lesson=self.lesson)
            .order_by("order")
            .values_list("block_type", "text_content")
        )

    def test_replaces_the_whole_body(self):
        response = self.client.put(
            self.url,
            [
                {"order": 1, "block_type": "HEADING_1", "text_content": "Intro"},
                {"order": 2, "block_type": "PARAGRAPH", "text_content": "Body text."},
            ],
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._blocks(), [("HEADING_1", "Intro"), ("PARAGRAPH", "Body text.")]
        )

    def test_second_save_replaces_rather_than_appends(self):
        self.client.put(
            self.url,
            [{"order": 1, "block_type": "PARAGRAPH", "text_content": "First"}],
            format="json",
        )
        self.client.put(
            self.url,
            [{"order": 1, "block_type": "PARAGRAPH", "text_content": "Second"}],
            format="json",
        )

        self.assertEqual(self._blocks(), [("PARAGRAPH", "Second")])

    def test_empty_list_clears_the_body(self):
        self.client.put(
            self.url,
            [{"order": 1, "block_type": "PARAGRAPH", "text_content": "Gone"}],
            format="json",
        )

        response = self.client.put(self.url, [], format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._blocks(), [])

    def test_invalid_block_rejects_the_whole_save(self):
        self.client.put(
            self.url,
            [{"order": 1, "block_type": "PARAGRAPH", "text_content": "Keep me"}],
            format="json",
        )

        # A prose block with no text is invalid; the existing body must survive.
        response = self.client.put(
            self.url,
            [
                {"order": 1, "block_type": "PARAGRAPH", "text_content": "New"},
                {"order": 2, "block_type": "PARAGRAPH", "text_content": ""},
            ],
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._blocks(), [("PARAGRAPH", "Keep me")])

    def test_outsider_cannot_write(self):
        self.client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))

        response = self.client.put(
            self.url,
            [{"order": 1, "block_type": "PARAGRAPH", "text_content": "Nope"}],
            format="json",
        )

        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )


class CoursePreviewTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.course = make_draft_course(creator=self.creator)
        self.url = f"/api/v1/courses/{self.course.id}/preview/"

    def test_returns_a_resolvable_token_for_a_draft(self):
        from api.courses.services import course_preview_service

        self.client.force_authenticate(self.creator)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("token=", response.data["preview_url"])
        self.assertIn(str(self.course.id), response.data["preview_url"])

        token = response.data["preview_url"].split("token=")[1]
        self.assertEqual(
            course_preview_service.resolve_preview_token(token), str(self.course.id)
        )

    def test_a_platform_access_token_is_not_a_preview_grant(self):
        """Both are signed with the same key; the type claim is the guard."""

        from rest_framework_simplejwt.tokens import AccessToken

        from api.courses.services import course_preview_service

        access = str(AccessToken.for_user(self.creator))

        with self.assertRaises(course_preview_service.PreviewTokenInvalid):
            course_preview_service.resolve_preview_token(access)

    def test_garbage_token_is_refused(self):
        from api.courses.services import course_preview_service

        with self.assertRaises(course_preview_service.PreviewTokenInvalid):
            course_preview_service.resolve_preview_token("not-a-token")

    def test_outsider_cannot_mint_a_preview(self):
        self.client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))

        response = self.client.get(self.url)

        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )


class CourseVersionTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.course = make_draft_course(creator=self.creator)
        self.client.force_authenticate(self.creator)

    def test_version_list_excludes_frozen_labels(self):
        from api.courses.models import CourseVersion

        active = CourseVersion.objects.create(label="9.0", is_active=True)
        CourseVersion.objects.create(label="9.9", is_active=False)

        response = self.client.get("/api/v1/course-versions/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        labels = [row["label"] for row in response.data["data"]["results"]]
        self.assertIn(active.label, labels)
        self.assertNotIn("9.9", labels)

    def test_version_can_be_set_on_a_draft(self):
        from api.courses.models import CourseVersion

        version = CourseVersion.objects.create(label="9.1", is_active=True)

        response = self.client.patch(
            f"/api/v1/courses/{self.course.id}/",
            {"version": str(version.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.course.refresh_from_db()
        self.assertEqual(self.course.version_id, version.id)

    def test_frozen_version_is_refused(self):
        from api.courses.models import CourseVersion

        frozen = CourseVersion.objects.create(label="9.2", is_active=False)

        response = self.client.patch(
            f"/api/v1/courses/{self.course.id}/",
            {"version": str(frozen.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.course.refresh_from_db()
        self.assertIsNone(self.course.version_id)


class ReorderRespectsLocksTests(APITestCase):
    """Reordering must not be a way around a module edit lock, since the
    ordinary PATCH path refuses to touch a module someone else holds."""

    def setUp(self):
        from api.collaborators.enums import CollaboratorRole
        from api.collaborators.tests.factories import make_collaborator

        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.holder = make_user(role=UserRole.COURSE_CREATOR)
        self.course = make_draft_course(creator=self.creator)
        make_collaborator(
            course=self.course, user=self.holder, role=CollaboratorRole.ADMIN
        )
        self.modules = [
            Module.objects.create(course=self.course, title=f"M{i}", order=i)
            for i in (1, 2)
        ]

    def _payload(self):
        a, b = self.modules
        return {
            "order": [
                {"id": str(b.id), "order": 1},
                {"id": str(a.id), "order": 2},
            ]
        }

    def test_module_reorder_refused_while_another_user_holds_a_lock(self):
        self.client.force_authenticate(self.holder)
        self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/{self.modules[0].id}/lock/"
        )

        self.client.force_authenticate(self.creator)
        response = self.client.patch(
            f"/api/v1/courses/{self.course.id}/modules/reorder/",
            self._payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_423_LOCKED)
        self.assertEqual(
            list(
                Module.objects.filter(course=self.course)
                .order_by("order")
                .values_list("title", flat=True)
            ),
            ["M1", "M2"],
        )

    def test_lock_holder_can_still_reorder(self):
        self.client.force_authenticate(self.creator)
        self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/{self.modules[0].id}/lock/"
        )

        response = self.client.patch(
            f"/api/v1/courses/{self.course.id}/modules/reorder/",
            self._payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
