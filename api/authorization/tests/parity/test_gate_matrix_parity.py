"""Converting gates to stored permissions must not change who passes them.

The baseline was generated from the role-class gates before any conversion
(`manage.py rbac_gate_matrix`). This recomputes the matrix with saved users -
so gates that read stored permissions resolve them for real - and requires an
exact match, apart from the deliberate differences declared below.
"""

import json
from pathlib import Path

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from api.authorization.gate_matrix import ANONYMOUS, SUPERUSER, compute_matrix
from api.courses.enums import CourseStatus
from api.courses.tests.factories import make_draft_course, make_user
from api.users.enums import UserRole

BASELINE_PATH = Path(__file__).resolve().parent / "gate_matrix_baseline.json"

QA_ACTIONS = ("qa_claim", "qa_approve", "qa_reject")

#: Invite handlers the recipient calls on their own invite (see D6).
INVITEE_SELF_SERVICE_HANDLERS = (
    "[CollaboratorInviteViewSet.retrieve]",
    "[CollaboratorInviteViewSet.incoming]",
    "[CollaboratorInviteViewSet.accept]",
    "[CollaboratorInviteViewSet.decline]",
)

#: Gates whose view class was replaced without changing who passes them.
RENAMED_GATES = {
    # The read-only roles catalogue became the Roles & Permissions list.
    "GET api/v1/admin/roles/ [RoleCatalogueView.get]": "GET api/v1/admin/roles/ [RoleListCreateView.get]",
    # The docs views gained a no-cache wrapper without changing who passes.
    "GET api/v1/docs/ [SpectacularSwaggerView.get]": "GET api/v1/docs/ [DocumentationSwaggerView.get]",
    "GET api/v1/docs/reviewer-settings/ [SpectacularSwaggerView.get]": "GET api/v1/docs/reviewer-settings/ [DocumentationSwaggerView.get]",
    "GET api/v1/redoc/ [SpectacularRedocView.get]": "GET api/v1/redoc/ [DocumentationRedocView.get]",
}


def apply_intentional_deltas(baseline: dict) -> dict:
    """The baseline plus the deliberate access changes of the conversion.

    Each rule has a reason and a behaviour test in IntentionalDeltaTests; any
    other difference from the baseline fails the parity test.
    """

    expected = {key: list(allowed) for key, allowed in baseline.items()}
    for key, allowed in expected.items():
        # D1: the QA actions' view gate is "may approve/reject reviews"; who
        # may sit the QA seat moved into review_service (QA_SEAT_ROLES), so
        # Creator Reviewers and Verifiers now pass the gate and are refused
        # by the service instead.
        if any(f".{action}]" in key for action in QA_ACTIONS):
            allowed.extend([UserRole.CREATOR_REVIEWER, UserRole.STAFF_VERIFIER])
        # D3: the Super Admin role holds every permission in code. The real
        # Super Admin is bootstrapped with is_superuser, which already passed
        # every gate, so only a SUPER_ADMIN row without is_superuser changes.
        if SUPERUSER in allowed and UserRole.SUPER_ADMIN not in allowed:
            allowed.append(UserRole.SUPER_ADMIN)
        # D4: the Writer gained courses.approve / courses.reject (migration
        # 0006) so it can sit the review seats of the staged review flow. Every
        # gate that admits a holder of those permissions now admits a Writer
        # at the view; the seat roles in review_service still refuse a Writer
        # a seat, and the QA seat, while the staged flow is off.
        if UserRole.QA_REVIEWER in allowed and UserRole.STAFF_WRITER not in allowed:
            allowed.append(UserRole.STAFF_WRITER)
        # D5: the Writer sets creator prices (catalog.set_creator_prices,
        # migration 0008), including a topic's, so topic updates admit it at
        # the view; topic_service then lets it change prices only, and topic
        # managers everything but prices.
        if key.endswith(("[TopicViewSet.update]", "[TopicViewSet.partial_update]")) and UserRole.STAFF_WRITER not in allowed:
            allowed.append(UserRole.STAFF_WRITER)
        # D6: a collaboration invite is addressed to an email, not to a role,
        # so the invitee-side handlers (read your own invite, /incoming/,
        # accept, decline) dropped courses.create for a plain authenticated
        # session. Under the old gate a recipient whose role lacks that
        # permission could never see - let alone accept - the invite that
        # would grant them access. The rows stay scoped to the caller's own
        # email in get_queryset, and invite_service re-checks the match, so
        # admitting every role at the view leaks nothing.
        if key.endswith(INVITEE_SELF_SERVICE_HANDLERS):
            allowed[:] = [*UserRole.values, SUPERUSER]
    return expected


class GateMatrixParityTests(TestCase):
    maxDiff = None

    def test_every_gate_admits_exactly_who_it_did_before(self):
        principals = {role: make_user(role=role) for role in UserRole.values}
        principals[SUPERUSER] = make_user(
            role=UserRole.COURSE_CREATOR, is_superuser=True
        )
        principals[ANONYMOUS] = AnonymousUser()
        expected = apply_intentional_deltas(json.loads(BASELINE_PATH.read_text()))

        actual = compute_matrix(principals)

        # Endpoints added since the baseline are not compared here (they have
        # their own tests); every baseline gate must still exist and match.
        for old_key, new_key in RENAMED_GATES.items():
            if old_key in expected:
                expected[new_key] = expected.pop(old_key)
        self.assertEqual(
            sorted(set(expected) - set(actual)), [], "baseline gates disappeared"
        )
        mismatches = {
            key: {"expected": sorted(expected[key]), "actual": sorted(actual[key])}
            for key in expected
            if sorted(expected[key]) != sorted(actual[key])
        }
        self.assertEqual(mismatches, {})


class IntentionalDeltaTests(APITestCase):
    """Behaviour behind each rule in apply_intentional_deltas."""

    def test_d4_writer_passes_the_review_gates_but_is_refused_the_seats_when_staged_flow_is_off(
        self,
    ):
        course = make_draft_course(status=CourseStatus.SUBMITTED)
        self.client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))

        listed = self.client.get("/api/v1/review-queue/")
        claimed = self.client.post(f"/api/v1/review-queue/{course.id}/claim/")
        qa = self.client.post(f"/api/v1/review-queue/{course.id}/qa-claim/")

        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertEqual(claimed.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(qa.status_code, status.HTTP_403_FORBIDDEN)

    def test_d6_invitee_without_courses_create_reaches_their_invite_but_not_others(
        self,
    ):
        from datetime import timedelta

        from django.utils import timezone

        from api.collaborators.models import CollaboratorInvite

        # CREATOR_REVIEWER holds no courses.create, so the old gate 403'd it.
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        course = make_draft_course()
        mine = CollaboratorInvite.objects.create(
            course=course,
            email=reviewer.email,
            invited_by=course.creator,
            expires_at=timezone.now() + timedelta(days=14),
        )
        someone_elses = CollaboratorInvite.objects.create(
            course=course,
            email="not-me@example.com",
            invited_by=course.creator,
            expires_at=timezone.now() + timedelta(days=14),
        )
        self.client.force_authenticate(reviewer)

        self.assertEqual(
            self.client.get("/api/v1/course-invites/incoming/").status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.get(f"/api/v1/course-invites/{mine.id}/").status_code,
            status.HTTP_200_OK,
        )
        # The gate opened; the queryset scoping is what still keeps other
        # people's invites invisible.
        self.assertEqual(
            self.client.get(f"/api/v1/course-invites/{someone_elses.id}/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_d1_creator_reviewer_is_refused_the_qa_seat_by_the_service(self):
        course = make_draft_course(status=CourseStatus.QA_VERIFICATION)
        for role in (UserRole.CREATOR_REVIEWER, UserRole.STAFF_VERIFIER):
            with self.subTest(role=role):
                self.client.force_authenticate(make_user(role=role))

                for action in ("qa-claim", "qa-approve", "qa-reject"):
                    response = self.client.post(
                        f"/api/v1/review-queue/{course.id}/{action}/",
                        {"feedback": {"summary": "x"}},
                        format="json",
                    )
                    self.assertEqual(
                        response.status_code, status.HTTP_403_FORBIDDEN, action
                    )

    def test_d3_super_admin_role_holds_creator_permissions(self):
        super_admin = make_user(role=UserRole.SUPER_ADMIN)
        self.client.force_authenticate(super_admin)

        response = self.client.get("/api/v1/course-versions/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_d5_the_writer_passes_the_topic_update_gate_but_may_only_change_prices(self):
        from api.courses.tests.factories import make_topic

        topic = make_topic()
        self.client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))

        renamed = self.client.patch(f"/api/v1/topics/{topic.id}/", {"name": "Renamed"}, format="json")
        repriced = self.client.patch(f"/api/v1/topics/{topic.id}/", {"creator_price_with_video": "9.00"}, format="json")

        self.assertEqual(renamed.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(repriced.status_code, status.HTTP_200_OK)
