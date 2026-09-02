from rest_framework import status
from rest_framework.test import APITestCase
from django.utils import timezone
from datetime import timedelta

from api.collaborators.tests.factories import make_collaborator
from api.courses.enums import CourseStatus, DistributionStatus
from api.courses.models import Course, CourseDistribution, CourseVersion
from api.courses.services import course_service
from api.courses.tests.factories import (
    build_compliant_course,
    make_category,
    make_draft_course,
    make_topic,
    make_user,
)
from api.users.enums import UserRole


class CourseApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.other_creator = make_user(role=UserRole.COURSE_CREATOR)
        self.admin = make_user(role=UserRole.ADMIN)
        self.category = make_category()
        # Publishing needs an active CourseVersion; get_or_create keeps the
        # test independent of whether the seed migration has run.
        CourseVersion.objects.get_or_create(label="1.0")

    def test_creator_can_create_draft_course(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/courses/",
            {
                "category": str(self.category.id),
                "title": "My Course",
                "description": "d" * 20,
                "preview_video_url": "https://example.com/p.mp4",
                "terms_accepted": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Course.objects.filter(creator=self.creator).count(), 1)

    def test_creator_can_create_with_topic_and_course_information_fields(self):
        topic = make_topic(category=self.category)
        version = CourseVersion.objects.get(label="1.0")
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/courses/",
            {
                "category": str(self.category.id),
                "topic": str(topic.id),
                "title": "My Course",
                "description": "d" * 20,
                "difficulty_level": "BEGINNER",
                "learning_objectives": ["Objective 1", "Objective 2"],
                "tags": ["Python", "Backend"],
                "version": str(version.id),
                "thumbnail_url": "https://example.com/thumb.jpg",
                "duration_hours": 1,
                "duration_minutes": 30,
                "duration_seconds": 0,
                "terms_accepted": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        course = Course.objects.get(creator=self.creator)
        self.assertEqual(course.topic_id, topic.id)
        self.assertEqual(course.difficulty_level, "BEGINNER")
        self.assertEqual(course.learning_objectives, ["Objective 1", "Objective 2"])
        self.assertEqual(course.tags, ["Python", "Backend"])
        self.assertEqual(course.version_id, version.id)
        self.assertEqual(response.data["version"]["label"], "1.0")
        self.assertEqual(course.thumbnail_url, "https://example.com/thumb.jpg")
        self.assertEqual(course.planned_duration_seconds, 90 * 60)

    def test_creator_can_list_active_course_versions(self):
        CourseVersion.objects.create(label="0.9", is_active=False)
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/course-versions/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [item["label"] for item in response.data["data"]["results"]], ["1.0"]
        )

    def test_creator_can_select_version_on_draft(self):
        version = CourseVersion.objects.get(label="1.0")
        course = make_draft_course(creator=self.creator, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/",
            {"version": str(version.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["version"]["label"], "1.0")
        course.refresh_from_db()
        self.assertEqual(course.version_id, version.id)

    def test_create_with_mismatched_topic_and_category_rejected(self):
        other_category = make_category()
        topic = make_topic(category=other_category)
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/courses/",
            {
                "category": str(self.category.id),
                "topic": str(topic.id),
                "title": "My Course",
                "description": "d" * 20,
                "terms_accepted": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_with_non_string_tags_rejected(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/courses/",
            {
                "category": str(self.category.id),
                "title": "My Course",
                "description": "d" * 20,
                "tags": ["ok", ""],
                "terms_accepted": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_without_duration_leaves_it_unchanged(self):
        course = make_draft_course(
            creator=self.creator, category=self.category, planned_duration_seconds=120
        )
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/", {"title": "New Title"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.planned_duration_seconds, 120)

    def test_partial_update_with_duration_recombines_it(self):
        course = make_draft_course(
            creator=self.creator, category=self.category, planned_duration_seconds=120
        )
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/",
            {"duration_hours": 2, "duration_minutes": 0, "duration_seconds": 0},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.planned_duration_seconds, 2 * 3600)

    def test_non_creator_role_forbidden_to_create(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/courses/",
            {
                "category": str(self.category.id),
                "title": "X",
                "description": "d",
                "terms_accepted": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_reviewer_role_forbidden_to_create(self):
        # Reviewers verify other people's work; authoring is not their job.
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.client.force_authenticate(reviewer)

        response = self.client.post(
            "/api/v1/courses/",
            {
                "category": str(self.category.id),
                "title": "X",
                "description": "d",
                "terms_accepted": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_creator_lists_only_own_courses(self):
        make_draft_course(creator=self.creator, category=self.category)
        make_draft_course(creator=self.other_creator, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/courses/")
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)

    def test_creator_course_list_supports_figma_filters(self):
        matching = make_draft_course(
            creator=self.creator,
            category=self.category,
            title="Python Analytics",
            source_type="CREATOR_UPLOADED",
            quality_score=80,
        )
        other_category = make_category()
        old_course = make_draft_course(
            creator=self.creator,
            category=other_category,
            title="Unrelated Course",
            source_type="AI_GENERATED",
            quality_score=20,
        )
        Course.objects.filter(id=old_course.id).update(
            created_datetime=timezone.now() - timedelta(days=30)
        )
        self.client.force_authenticate(self.creator)

        response = self.client.get(
            "/api/v1/courses/",
            {
                "course_id": str(matching.id),
                "search": "Python",
                "category": str(self.category.id),
                "status": "DRAFT",
                "creator_type": "CREATOR_UPLOADED",
                "quality_score": 80,
                "date_from": timezone.localdate().isoformat(),
                "date_to": timezone.localdate().isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertEqual([row["id"] for row in results], [str(matching.id)])

    def test_creator_type_alias_matches_source_type_filter(self):
        matching = make_draft_course(
            creator=self.creator,
            category=self.category,
            source_type="AI_GENERATED",
        )
        make_draft_course(
            creator=self.creator,
            category=self.category,
            source_type="CREATOR_UPLOADED",
        )
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/courses/", {"creator_type": "AI_GENERATED"})

        results = response.data["data"]["results"]
        self.assertEqual([row["id"] for row in results], [str(matching.id)])

    def test_creator_cannot_retrieve_others_course(self):
        course = make_draft_course(creator=self.other_creator, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.get(f"/api/v1/courses/{course.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_can_update_draft(self):
        course = make_draft_course(creator=self.creator, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/", {"title": "New Title"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.title, "New Title")

    def test_cannot_update_once_submitted(self):
        course = build_compliant_course(creator=self.creator, category=self.category)
        course_service.submit_course(course=course, actor=self.creator)
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/", {"title": "New Title"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_collaborator_can_retrieve_and_update_draft(self):
        course = make_draft_course(creator=self.creator, category=self.category)
        collaborator_user = make_user()
        make_collaborator(course=course, user=collaborator_user)
        self.client.force_authenticate(collaborator_user)

        response = self.client.get(f"/api/v1/courses/{course.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/", {"title": "Collab Title"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.title, "Collab Title")

    def test_collaborator_cannot_submit_or_delete(self):
        course = build_compliant_course(creator=self.creator, category=self.category)
        collaborator_user = make_user()
        make_collaborator(course=course, user=collaborator_user)
        self.client.force_authenticate(collaborator_user)

        response = self.client.post(f"/api/v1/courses/{course.id}/submit/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        response = self.client.delete(f"/api/v1/courses/{course.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_can_delete_draft(self):
        course = make_draft_course(creator=self.creator, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.delete(f"/api/v1/courses/{course.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_submit_happy_path(self):
        course = build_compliant_course(creator=self.creator, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.post(f"/api/v1/courses/{course.id}/submit/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.SUBMITTED)

    def test_submit_fails_when_standards_not_met(self):
        course = make_draft_course(creator=self.creator, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.post(f"/api/v1/courses/{course.id}/submit/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_publish_is_admin_only(self):
        course = build_compliant_course(creator=self.creator, category=self.category)
        course.status = CourseStatus.APPROVED
        course.save()
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/courses/{course.id}/publish/",
            {
                "distribution_channels": [
                    {
                        "channel": "SOLUDESK",
                        "learner_price": "149.00",
                        "model": "ONE_TIME",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_publish_approved_course(self):
        course = build_compliant_course(creator=self.creator, category=self.category)
        course.status = CourseStatus.APPROVED
        course.save()
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"/api/v1/courses/{course.id}/publish/",
            {
                "distribution_channels": [
                    {
                        "channel": "SOLUDESK",
                        "learner_price": "149.00",
                        "model": "ONE_TIME",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.PUBLISHED)

    def test_publish_wrong_source_status(self):
        course = build_compliant_course(
            creator=self.creator, category=self.category
        )  # still Draft
        self.client.force_authenticate(self.admin)

        response = self.client.post(f"/api/v1/courses/{course.id}/publish/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_saves_design_pricing_fields_and_publishes_channels(self):
        course = build_compliant_course(creator=self.creator, category=self.category)
        course.status = CourseStatus.APPROVED
        course.creator_price_snapshot = "150.00"
        course.save()
        self.client.force_authenticate(self.admin)
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
                },
            ]
        }

        save_response = self.client.put(
            f"/api/v1/courses/{course.id}/review-prices/", payload, format="json"
        )
        self.assertEqual(save_response.status_code, status.HTTP_200_OK)
        self.assertEqual(save_response.data[0]["creator_payout_fixed"], "150.00")
        self.assertEqual(save_response.data[0]["mie_suggestion"], "140.00")
        self.assertEqual(save_response.data[0]["model"], "ONE_TIME")
        self.assertNotIn("mie_suggested_price", save_response.data[0])
        self.assertNotIn("pricing_model", save_response.data[0])

        response = self.client.post(
            f"/api/v1/courses/{course.id}/publish/", payload, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["distribution_channels"]), 2)
        self.assertEqual(
            CourseDistribution.objects.get(course=course, channel="SOLUDESK").status,
            DistributionStatus.PUBLISHED,
        )
        self.assertEqual(
            CourseDistribution.objects.get(course=course, channel="UDEMY").status,
            DistributionStatus.QUEUED,
        )


class AdminCourseCrudTests(APITestCase):
    """Admins and invited Approvers manage every course, not just their own."""

    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.admin = make_user(role=UserRole.ADMIN)
        self.approver = make_user(role=UserRole.STAFF_APPROVER)
        self.category = make_category()

    def test_admin_cannot_create_course(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/courses/",
            {
                "category": str(self.category.id),
                "title": "Admin Authored Course",
                "description": "d" * 20,
                "terms_accepted": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_approver_cannot_create_course(self):
        self.client.force_authenticate(self.approver)

        response = self.client.post(
            "/api/v1/courses/",
            {
                "category": str(self.category.id),
                "title": "Approver Authored Course",
                "description": "d" * 20,
                "terms_accepted": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_lists_all_courses_not_just_own(self):
        make_draft_course(creator=self.creator, category=self.category)
        make_draft_course(creator=self.admin, category=self.category)
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/courses/")

        self.assertEqual(len(response.data["data"]["results"]), 2)

    def test_admin_can_retrieve_others_course(self):
        course = make_draft_course(creator=self.creator, category=self.category)
        self.client.force_authenticate(self.admin)

        response = self.client.get(f"/api/v1/courses/{course.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_can_update_others_draft(self):
        course = make_draft_course(creator=self.creator, category=self.category)
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/", {"title": "Retitled"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.title, "Retitled")

    def test_admin_can_delete_others_draft(self):
        course = make_draft_course(creator=self.creator, category=self.category)
        self.client.force_authenticate(self.admin)

        response = self.client.delete(f"/api/v1/courses/{course.id}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Course.objects.filter(id=course.id).exists())

    def test_admin_still_bound_by_workflow_rules(self):
        # Wider access, same business rules: only Drafts are editable.
        course = build_compliant_course(creator=self.creator, category=self.category)
        course_service.submit_course(course=course, actor=self.creator)
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/", {"title": "Retitled"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_cannot_submit_someone_elses_course(self):
        # Submitting is the author vouching for their own work.
        course = build_compliant_course(creator=self.creator, category=self.category)
        self.client.force_authenticate(self.admin)

        response = self.client.post(f"/api/v1/courses/{course.id}/submit/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.DRAFT)

    def test_admin_can_submit_own_course(self):
        course = build_compliant_course(creator=self.admin, category=self.category)
        self.client.force_authenticate(self.admin)

        response = self.client.post(f"/api/v1/courses/{course.id}/submit/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.SUBMITTED)

    def test_creator_still_cannot_reach_others_course(self):
        # The admin widening must not leak into ordinary creator access.
        other = make_user(role=UserRole.COURSE_CREATOR)
        course = make_draft_course(creator=other, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/", {"title": "Nope"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
