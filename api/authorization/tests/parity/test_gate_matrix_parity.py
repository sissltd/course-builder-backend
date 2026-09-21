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
