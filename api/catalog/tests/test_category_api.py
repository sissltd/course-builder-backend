"""Coverage for category browsing and staff-managed category CRUD.

The access rule under test is deliberately narrow: Writers, Admins, and Super
Admins manage categories, everyone else reads them. Approvers are included in
"everyone else" - unlike courses, where they have full control - so several
tests below assert a 403 for roles that are privileged elsewhere.
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from api.catalog.enums import CategoryStatus, TrackPreference
from api.catalog.models import Category
from api.catalog.tests.factories import make_category, make_user
from api.users.enums import UserRole

LIST_URL = "/api/v1/categories/"


def _force_authenticate_mfa_verified(client, user):
    """ADMIN/SUPER_ADMIN are MFA-mandated roles - IsMFAVerifiedForSession
    requires the token to carry mfa_verified=True, which plain
    force_authenticate(user) (no token) never does."""

    token = AccessToken.for_user(user)
    token["mfa_verified"] = True
    client.force_authenticate(user, token=token)


VALID_PAYLOAD = {
    "name": "New Cat",
    "creator_price": "100.00",
    "track_preference": "OPEN",
    "status": "ACTIVE",
}


def detail_url(category):
    return f"/api/v1/categories/{category.id}/"


class CategoryReadAccessTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)

    def test_list_requires_authentication(self):
        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_can_list_categories(self):
        make_category(name="Web Dev", track_preference=TrackPreference.OPEN)
        self.client.force_authenticate(self.creator)

        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]["results"]), 1)

    def test_every_role_can_read_categories(self):
        make_category(name="Readable")

        for role in [
            UserRole.COURSE_CREATOR,
            UserRole.CREATOR_REVIEWER,
            UserRole.STAFF_WRITER,
            UserRole.STAFF_VERIFIER,
            UserRole.STAFF_APPROVER,
            UserRole.ADMIN,
        ]:
            with self.subTest(role=role):
                self.client.force_authenticate(make_user(role=role))
                response = self.client.get(LIST_URL)
                self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_filter_by_track_preference(self):
        make_category(name="Web Dev", track_preference=TrackPreference.OPEN)
        make_category(name="AI 101", track_preference=TrackPreference.AI_PREFERRED)
        self.client.force_authenticate(self.creator)

        response = self.client.get(LIST_URL, {"track_preference": "AI_PREFERRED"})

        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "AI 101")

    def test_filter_by_status_for_the_creation_picker(self):
        make_category(name="Open", status=CategoryStatus.ACTIVE)
        make_category(name="Closed", status=CategoryStatus.INACTIVE)
        self.client.force_authenticate(self.creator)

        response = self.client.get(LIST_URL, {"status": "ACTIVE"})

        results = response.data["data"]["results"]
        self.assertEqual([row["name"] for row in results], ["Open"])


class CategoryWriteAccessTests(APITestCase):
    """Only Writers, Admins, and Super Admins may create/update/delete."""

    def test_writer_can_create(self):
        self.client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))

        response = self.client.post(LIST_URL, VALID_PAYLOAD)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Category.objects.filter(name="New Cat").exists())

    def test_super_admin_can_create(self):
        _force_authenticate_mfa_verified(
            self.client, make_user(role=UserRole.SUPER_ADMIN)
        )

        response = self.client.post(LIST_URL, VALID_PAYLOAD)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_admin_can_create(self):
        _force_authenticate_mfa_verified(self.client, make_user(role=UserRole.ADMIN))

        response = self.client.post(LIST_URL, VALID_PAYLOAD)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Category.objects.filter(name="New Cat").exists())

    def test_approver_cannot_create(self):
        self.client.force_authenticate(make_user(role=UserRole.STAFF_APPROVER))

        response = self.client.post(LIST_URL, VALID_PAYLOAD)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_public_course_creator_cannot_create(self):
        # The distinction that motivated a separate STAFF_WRITER role: a
        # self-registered creator must not manage categories.
        self.client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))

        response = self.client.post(LIST_URL, VALID_PAYLOAD)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_verifier_cannot_create(self):
        self.client.force_authenticate(make_user(role=UserRole.STAFF_VERIFIER))

        response = self.client.post(LIST_URL, VALID_PAYLOAD)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_create(self):
        response = self.client.post(LIST_URL, VALID_PAYLOAD)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_can_update_and_delete(self):
        category = make_category()
        _force_authenticate_mfa_verified(self.client, make_user(role=UserRole.ADMIN))

        patch = self.client.patch(
            detail_url(category), {"name": "Renamed"}, format="json"
        )
        self.assertEqual(patch.status_code, status.HTTP_200_OK)

        delete = self.client.delete(detail_url(category))
        self.assertEqual(delete.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Category.objects.filter(id=category.id).exists())


class CategoryWriteBehaviourTests(APITestCase):
    def setUp(self):
        self.writer = make_user(role=UserRole.STAFF_WRITER)
        self.client.force_authenticate(self.writer)

    def test_create_records_the_acting_staff_member(self):
        response = self.client.post(LIST_URL, VALID_PAYLOAD)

        category = Category.objects.get(id=response.data["id"])
        self.assertEqual(category.created_by, self.writer)
        self.assertEqual(category.updated_by, self.writer)

    def test_create_returns_the_read_representation(self):
        response = self.client.post(LIST_URL, VALID_PAYLOAD)

        # Read shape includes timestamps the write serializer does not accept.
        self.assertIn("created_datetime", response.data)
        self.assertEqual(response.data["name"], "New Cat")

    def test_writer_can_update_price(self):
        category = make_category(creator_price=Decimal("50.00"))

        response = self.client.patch(
            detail_url(category), {"creator_price": "75.00"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        category.refresh_from_db()
        self.assertEqual(category.creator_price, Decimal("75.00"))

    def test_update_records_the_editor(self):
        category = make_category()

        self.client.patch(detail_url(category), {"name": "Renamed"}, format="json")

        category.refresh_from_db()
        self.assertEqual(category.updated_by, self.writer)

    def test_closing_a_category_keeps_it_readable(self):
        category = make_category(status=CategoryStatus.ACTIVE)

        response = self.client.patch(
            detail_url(category), {"status": "INACTIVE"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        category.refresh_from_db()
        self.assertEqual(category.status, CategoryStatus.INACTIVE)

    def test_writer_can_delete_unused_category(self):
        category = make_category()

        response = self.client.delete(detail_url(category))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Category.objects.filter(id=category.id).exists())

    def test_duplicate_name_rejected(self):
        make_category(name="Dup")

        response = self.client.post(LIST_URL, {**VALID_PAYLOAD, "name": "Dup"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_negative_price_rejected(self):
        response = self.client.post(
            LIST_URL, {**VALID_PAYLOAD, "creator_price": "-1.00"}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CategoryDeletionImpactTests(APITestCase):
    """The warning shown before an admin commits to deleting a category."""

    def setUp(self):
        self.writer = make_user(role=UserRole.STAFF_WRITER)
        self.client.force_authenticate(self.writer)

    def impact_url(self, category):
        return f"/api/v1/categories/{category.id}/deletion-impact/"

    def test_empty_category_needs_no_strategy(self):
        category = make_category()

        response = self.client.get(self.impact_url(category))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["course_count"], 0)
        self.assertFalse(response.data["requires_strategy"])

    def test_counts_courses_by_status(self):
        from api.courses.enums import CourseStatus
        from api.courses.tests.factories import make_draft_course

        category = make_category()
        make_draft_course(category=category)
        published = make_draft_course(category=category)
        published.status = CourseStatus.PUBLISHED
        published.save()

        response = self.client.get(self.impact_url(category))

        self.assertEqual(response.data["course_count"], 2)
        self.assertEqual(response.data["courses_by_status"]["DRAFT"], 1)
        self.assertEqual(response.data["courses_by_status"]["PUBLISHED"], 1)
        self.assertTrue(response.data["requires_strategy"])

    def test_reports_affected_creator_profiles(self):
        from api.onboarding.models import CreatorProfile

        category = make_category()
        CreatorProfile.objects.create(
            user=make_user(), primary_expertise_category=category
        )

        response = self.client.get(self.impact_url(category))

        self.assertEqual(response.data["affected_creator_profile_count"], 1)

    def test_impact_does_not_change_anything(self):
        from api.courses.models import Course
        from api.courses.tests.factories import make_draft_course

        category = make_category()
        make_draft_course(category=category)

        self.client.get(self.impact_url(category))

        self.assertTrue(Category.objects.filter(id=category.id).exists())
        self.assertEqual(Course.objects.filter(category=category).count(), 1)

    def test_non_manager_cannot_preview(self):
        category = make_category()
        self.client.force_authenticate(make_user(role=UserRole.STAFF_APPROVER))

        response = self.client.get(self.impact_url(category))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CategoryDeletionStrategyTests(APITestCase):
    """Deleting a category that still holds courses."""

    def setUp(self):
        self.writer = make_user(role=UserRole.STAFF_WRITER)
        self.client.force_authenticate(self.writer)
        self.category = make_category(name="Doomed")
        self.replacement = make_category(name="Survivor")

    def _add_course(self, **kwargs):
        from api.courses.tests.factories import make_draft_course

        return make_draft_course(category=self.category, **kwargs)

    def test_no_strategy_is_refused_with_409_and_changes_nothing(self):
        from api.courses.models import Course

        self._add_course()

        response = self.client.delete(detail_url(self.category))

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(Category.objects.filter(id=self.category.id).exists())
        self.assertEqual(Course.objects.filter(category=self.category).count(), 1)

    def test_409_message_names_the_category_and_count(self):
        self._add_course()
        self._add_course()

        response = self.client.delete(detail_url(self.category))

        message = str(response.data)
        self.assertIn("Doomed", message)
        self.assertIn("2", message)

    def test_reassign_moves_courses_and_deletes_the_category(self):
        course = self._add_course()

        response = self.client.delete(
            f"{detail_url(self.category)}"
            f"?strategy=REASSIGN&replacement_category={self.replacement.id}"
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Category.objects.filter(id=self.category.id).exists())
        course.refresh_from_db()
        self.assertEqual(course.category_id, self.replacement.id)

    def test_reassign_records_the_actor_on_moved_courses(self):
        course = self._add_course()

        self.client.delete(
            f"{detail_url(self.category)}"
            f"?strategy=REASSIGN&replacement_category={self.replacement.id}"
        )

        course.refresh_from_db()
        self.assertEqual(course.updated_by, self.writer)

    def test_reassign_preserves_a_submitted_courses_frozen_price(self):
        # Moving a course must not re-price work already submitted.
        from api.courses.services import course_service
        from api.courses.tests.factories import build_compliant_course

        course = build_compliant_course(category=self.category)
        course_service.submit_course(course=course, actor=course.creator)
        course.refresh_from_db()
        frozen = course.creator_price_snapshot

        self.client.delete(
            f"{detail_url(self.category)}"
            f"?strategy=REASSIGN&replacement_category={self.replacement.id}"
        )

        course.refresh_from_db()
        self.assertEqual(course.creator_price_snapshot, frozen)

    def test_reassign_without_replacement_rejected(self):
        from api.courses.models import Course

        self._add_course()

        response = self.client.delete(f"{detail_url(self.category)}?strategy=REASSIGN")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(Category.objects.filter(id=self.category.id).exists())
        self.assertEqual(Course.objects.filter(category=self.category).count(), 1)

    def test_reassign_to_itself_rejected(self):
        self._add_course()

        response = self.client.delete(
            f"{detail_url(self.category)}"
            f"?strategy=REASSIGN&replacement_category={self.category.id}"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(Category.objects.filter(id=self.category.id).exists())

    def test_reassign_to_unknown_category_rejected(self):
        self._add_course()

        response = self.client.delete(
            f"{detail_url(self.category)}?strategy=REASSIGN"
            "&replacement_category=00000000-0000-0000-0000-000000000000"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_courses_removes_them_with_the_category(self):
        from api.courses.models import Course

        course = self._add_course()

        response = self.client.delete(
            f"{detail_url(self.category)}?strategy=DELETE_COURSES"
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Category.objects.filter(id=self.category.id).exists())
        self.assertFalse(Course.objects.filter(id=course.id).exists())

    def test_delete_courses_cascades_to_modules_and_lessons(self):
        from api.courses.models import Lesson, Module
        from api.courses.tests.factories import build_compliant_course

        course = build_compliant_course(category=self.category)
        self.assertTrue(Module.objects.filter(course=course).exists())

        self.client.delete(f"{detail_url(self.category)}?strategy=DELETE_COURSES")

        self.assertFalse(Module.objects.filter(course=course).exists())
        self.assertFalse(Lesson.objects.filter(module__course=course).exists())

    def test_delete_courses_removes_published_work_too(self):
        # Not limited to drafts - worth pinning, since it is the destructive edge.
        from api.courses.enums import CourseStatus
        from api.courses.models import Course

        course = self._add_course()
        course.status = CourseStatus.PUBLISHED
        course.save()

        self.client.delete(f"{detail_url(self.category)}?strategy=DELETE_COURSES")

        self.assertFalse(Course.objects.filter(id=course.id).exists())

    def test_other_categories_courses_are_untouched(self):
        from api.courses.models import Course
        from api.courses.tests.factories import make_draft_course

        mine = self._add_course()
        theirs = make_draft_course(category=self.replacement)

        self.client.delete(f"{detail_url(self.category)}?strategy=DELETE_COURSES")

        self.assertFalse(Course.objects.filter(id=mine.id).exists())
        self.assertTrue(Course.objects.filter(id=theirs.id).exists())

    def test_creator_profile_survives_with_a_null_category(self):
        from api.onboarding.models import CreatorProfile

        profile = CreatorProfile.objects.create(
            user=make_user(), primary_expertise_category=self.category
        )
        self._add_course()

        self.client.delete(f"{detail_url(self.category)}?strategy=DELETE_COURSES")

        profile.refresh_from_db()
        self.assertIsNone(profile.primary_expertise_category_id)

    def test_invalid_strategy_rejected(self):
        self._add_course()

        response = self.client.delete(f"{detail_url(self.category)}?strategy=NONSENSE")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(Category.objects.filter(id=self.category.id).exists())

    def test_empty_category_still_deletes_without_a_strategy(self):
        empty = make_category(name="Empty")

        response = self.client.delete(detail_url(empty))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_approver_cannot_delete_even_with_a_strategy(self):
        self._add_course()
        self.client.force_authenticate(make_user(role=UserRole.STAFF_APPROVER))

        response = self.client.delete(
            f"{detail_url(self.category)}?strategy=DELETE_COURSES"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Category.objects.filter(id=self.category.id).exists())
