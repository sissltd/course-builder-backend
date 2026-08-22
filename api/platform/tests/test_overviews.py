from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.tests.factories import make_draft_course, make_user
from api.reviews.enums import ReviewActionType
from api.reviews.models import ReviewAction
from api.users.enums import UserRole


class CreatorOverviewApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.other_creator = make_user(role=UserRole.COURSE_CREATOR)

    def test_requires_authentication(self):
        response = self.client.get("/api/v1/creator/overview/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_reviewer_role_is_forbidden(self):
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.client.force_authenticate(reviewer)
        response = self.client.get("/api/v1/creator/overview/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_counts_only_the_callers_own_courses_with_zero_filled_statuses(self):
        make_draft_course(creator=self.creator, title="Mine A")
        mine_b = make_draft_course(creator=self.creator, title="Mine B")
        mine_b.status = CourseStatus.SUBMITTED
        mine_b.save(update_fields=["status"])
        # Another creator's course must not leak into the counts.
        make_draft_course(creator=self.other_creator)

        self.client.force_authenticate(self.creator)
        response = self.client.get("/api/v1/creator/overview/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(set(response.data["courses"]), set(CourseStatus.values))
        self.assertEqual(response.data["courses"]["DRAFT"], 1)
        self.assertEqual(response.data["courses"]["SUBMITTED"], 1)
        self.assertEqual(response.data["courses"][CourseStatus.PUBLISHED], 0)

    def test_wallet_figures_reflect_transactions(self):
        from api.wallet.services import wallet_service

        wallet_service.credit_wallet(user=self.creator, amount=Decimal("90.00"))
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/creator/overview/")
        wallet = response.data["wallet"]
        self.assertEqual(wallet["balance"], "90.00")
        self.assertEqual(wallet["total_earned"], "90.00")
        self.assertEqual(wallet["pending_balance"], "0.00")
        self.assertEqual(wallet["currency"], "USD")

    def test_pending_invites_counted_for_my_email(self):
        from api.collaborators.models import CollaboratorInvite

        CollaboratorInvite.objects.create(
            course=make_draft_course(),
            email=self.creator.email,
            invited_by=self.other_creator,
            expires_at=timezone.now() + timedelta(days=3),
        )
        # An expired invite and someone else's invite don't count.
        CollaboratorInvite.objects.create(
            course=make_draft_course(),
            email=self.creator.email,
            invited_by=self.other_creator,
            expires_at=timezone.now() - timedelta(days=1),
        )
        CollaboratorInvite.objects.create(
            course=make_draft_course(),
            email="someone.else@example.com",
            invited_by=self.other_creator,
            expires_at=timezone.now() + timedelta(days=3),
        )

        self.client.force_authenticate(self.creator)
        response = self.client.get("/api/v1/creator/overview/")
        self.assertEqual(response.data["pending_invites"], 1)


class ReviewerOverviewApiTests(APITestCase):
    def setUp(self):
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)

    def test_requires_authentication(self):
        response = self.client.get("/api/v1/reviewer/overview/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_creator_role_is_forbidden(self):
        self.client.force_authenticate(self.creator)
        response = self.client.get("/api/v1/reviewer/overview/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_queue_counts_only_reviewable_states(self):
        submitted = make_draft_course()
        submitted.status = CourseStatus.SUBMITTED
        submitted.save(update_fields=["status"])
        in_review = make_draft_course()
        in_review.status = CourseStatus.IN_REVIEW
        in_review.save(update_fields=["status"])
        # Non-reviewable statuses never appear in the queue.
        published = make_draft_course()
        published.status = CourseStatus.PUBLISHED
        published.save(update_fields=["status"])

        self.client.force_authenticate(self.reviewer)
        response = self.client.get("/api/v1/reviewer/overview/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["queue"],
            {"SUBMITTED": 1, "IN_REVIEW": 1},
        )

    def test_my_decisions_counts_lifetime_and_today(self):
        reviewer2 = make_user(role=UserRole.CREATOR_REVIEWER)
        course_a = make_draft_course()
        course_b = make_draft_course()
        ReviewAction.objects.create(
            course=course_a,
            reviewer=self.reviewer,
            action=ReviewActionType.APPROVE,
        )
        ReviewAction.objects.create(
            course=course_b,
            reviewer=self.reviewer,
            action=ReviewActionType.REJECT,
        )
        ReviewAction.objects.create(
            course=course_a,
            reviewer=reviewer2,
            action=ReviewActionType.APPROVE,
        )

        self.client.force_authenticate(self.reviewer)
        response = self.client.get("/api/v1/reviewer/overview/")
        decisions = response.data["my_decisions"]
        self.assertEqual(decisions["approved"], 1)  # only their own approve
        self.assertEqual(decisions["today"], 2)  # both of their actions today
