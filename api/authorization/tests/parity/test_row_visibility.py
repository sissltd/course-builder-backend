"""Which rows each role sees on list endpoints whose querysets branch on role.

The gate-matrix parity test only sees view-level gates. These lists decide
"see everyone's rows" versus "see only your own" inside the queryset, so they
are pinned separately, over HTTP, for every principal. Expectations were
written from the code before any conversion to stored permissions.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from api.catalog.models import CategoryRequest
from api.courses.enums import CourseStatus
from api.courses.tests.factories import (
    make_course_appeal,
    make_draft_course,
    make_rejected_course,
    make_topic_reservation_request,
    make_user,
)
from api.payments.models import BankAccount
from api.quizzes.models import Question, Quiz
from api.reviews.models import QualityCheckCriterion
from api.users.enums import UserRole

ALL = "sees others' rows"
OWN = "sees only own rows"
DENIED = "refused"

CC = UserRole.COURSE_CREATOR
CR = UserRole.CREATOR_REVIEWER
W = UserRole.STAFF_WRITER
V = UserRole.STAFF_VERIFIER
AP = UserRole.STAFF_APPROVER
AI = UserRole.AI_REVIEWER
QA = UserRole.QA_REVIEWER
AD = UserRole.ADMIN
SA = UserRole.SUPER_ADMIN
SUPERUSER = "SUPERUSER"


def _expect(**by_principal):
    """Fill every principal not named with DENIED."""

    table = dict.fromkeys([*UserRole.values, SUPERUSER], DENIED)
    table.update(by_principal)
    return table


def _row_ids(response):
    data = response.data
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict) and "results" in data:
        data = data["results"]
    return {str(row["id"]) for row in data}


class RowVisibilityTests(TestCase):
    maxDiff = None

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(role=CC, first_name="Zebraowner")
        cls.principals = {role: make_user(role=role) for role in UserRole.values}
        cls.principals[SUPERUSER] = make_user(role=CC, is_superuser=True)

    def _outcomes(self, url, row_id, params=None):
        outcomes = {}
        for label, user in self.principals.items():
            client = APIClient()
            client.force_authenticate(user)
            response = client.get(url, params or {})
            if response.status_code == 403:
                outcomes[label] = DENIED
                continue
            self.assertEqual(response.status_code, 200, (label, url, response.data))
            outcomes[label] = ALL if str(row_id) in _row_ids(response) else OWN
        return outcomes

    def test_courses(self):
        course = make_draft_course(creator=self.owner)

        self.assertEqual(
            self._outcomes("/api/v1/courses/", course.id),
            _expect(**{CC: OWN, W: OWN, AP: ALL, AD: ALL, SA: ALL, SUPERUSER: ALL}),
        )

    def test_quizzes_and_questions(self):
        course = make_draft_course(creator=self.owner)
        quiz = Quiz.objects.create(level="COURSE", title="Q", course=course)
        question = Question.objects.create(
            quiz=quiz, question_text="Why?", question_type=Question.TypeChoices.ESSAY
        )
        expected = _expect(
            **{CC: OWN, W: OWN, AP: ALL, AD: ALL, SA: ALL, SUPERUSER: ALL}
        )

        self.assertEqual(self._outcomes("/api/v1/quizzes/", quiz.id), expected)
        self.assertEqual(self._outcomes("/api/v1/questions/", question.id), expected)

    def test_topic_reservation_requests(self):
        request = make_topic_reservation_request(requested_by=self.owner)

        self.assertEqual(
            self._outcomes("/api/v1/topic-reservations/", request.id),
            _expect(
                **{
                    CC: OWN,
                    W: OWN,
                    CR: ALL,
                    V: ALL,
                    AP: ALL,
                    AD: ALL,
                    SA: ALL,
                    SUPERUSER: ALL,
                }
            ),
        )

    def test_category_requests(self):
        request = CategoryRequest.objects.create(
            requested_by=self.owner, name="Robotics"
        )

        self.assertEqual(
            self._outcomes("/api/v1/category-requests/", request.id),
            _expect(**{CC: OWN, W: ALL, AD: ALL, SA: ALL, SUPERUSER: ALL}),
        )

    def test_course_appeals(self):
        appeal = make_course_appeal(
            course=make_rejected_course(creator=self.owner), submitted_by=self.owner
        )

        self.assertEqual(
            self._outcomes("/api/v1/course-appeals/", appeal.id),
            _expect(**{CC: OWN, W: OWN, AD: ALL, SA: ALL, SUPERUSER: ALL}),
        )

    def test_inactive_quality_criteria(self):
        retired = QualityCheckCriterion.objects.create(
            section="Thumbnail", label="Retired check", is_active=False
        )

        self.assertEqual(
            self._outcomes("/api/v1/quality-check-criteria/", retired.id),
            _expect(**{CC: OWN, W: OWN, AD: ALL, SA: ALL, SUPERUSER: ALL}),
        )

    def test_payout_accounts(self):
        account = BankAccount.objects.create(
            user=self.owner,
            bank_name="Bank",
            account_name="Zebra Owner",
            account_number="0123456789",
            bank_code="058",
        )
        # Every authenticated caller reaches it; only View Wallet holders
        # (Admin and Super Admin by default) see others' accounts.
        # Intentional delta D2: this was a hardcoded role check with no
        # superuser bypass; as a permission check, superusers now see all too.
        expected = dict.fromkeys([*UserRole.values, SUPERUSER], OWN)
        expected.update({AD: ALL, SA: ALL, SUPERUSER: ALL})

        self.assertEqual(
            self._outcomes("/api/v1/payout-accounts/", account.id), expected
        )

    def test_global_search_scopes(self):
        course = make_draft_course(
            creator=self.owner, title="Zebracourse", status=CourseStatus.SUBMITTED
        )
        course_seen, users_seen = {}, {}
        for label, user in self.principals.items():
            client = APIClient()
            client.force_authenticate(user)
            response = client.get("/api/v1/global-search/", {"q": "Zebra"})
            self.assertEqual(response.status_code, 200, label)
            buckets = response.data["results"]
            course_seen[label] = str(course.id) in {
                item["id"] for item in buckets.get("courses", {}).get("results", [])
            }
            users_seen[label] = "users" in buckets

        self.assertEqual(
            course_seen,
            {
                CC: False,
                W: False,
                CR: True,
                V: True,
                AP: True,
                QA: True,
                AI: False,
                AD: True,
                SA: True,
                SUPERUSER: True,
            },
        )
        self.assertEqual(
            users_seen,
            {
                CC: False,
                W: False,
                CR: False,
                V: False,
                AP: False,
                QA: False,
                AI: False,
                AD: True,
                SA: True,
                SUPERUSER: True,
            },
        )
