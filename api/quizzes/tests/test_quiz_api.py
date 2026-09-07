from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.models import Course, Lesson, Module
from api.courses.tests.factories import make_category, make_user
from api.quizzes.models import Question, QuestionOption, Quiz
from api.quizzes.serializers import QuestionSerializer
from api.users.enums import UserRole


class QuizApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.admin = make_user(role=UserRole.ADMIN)
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.category = make_category()
        self.course = Course.objects.create(
            title="Quiz fixture course",
            creator=self.creator,
            category=self.category,
            description="A course used as a quiz parent in tests.",
            terms_accepted_at=timezone.now(),
        )
        self.module = Module.objects.create(
            course=self.course, title="Module 1", order=1
        )
        self.lesson = Lesson.objects.create(
            module=self.module,
            title="Lesson 1",
            order=1,
            duration_minutes=10,
            script="A script of sufficient length for the model.",
        )

    def _quiz_payload(self, **overrides):
        payload = {
            "level": "COURSE",
            "title": "Final quiz",
            "description": "Checks overall comprehension.",
            "course": str(self.course.id),
            "passing_score": 70,
            "questions": [
                {
                    "question_text": "What is 2 + 2?",
                    "question_type": "MULTIPLE_CHOICE",
                    "points": 5,
                    "order": 1,
                    "options": [
                        {"option_text": "3", "is_correct": False, "order": 1},
                        {"option_text": "4", "is_correct": True, "order": 2},
                    ],
                },
                {
                    "question_text": "Explain recursion.",
                    "question_type": "ESSAY",
                    "points": 10,
                    "order": 2,
                    "model_response_guide": "Mention base case and self-reference.",
                },
            ],
        }
        payload.update(overrides)
        return payload

    def _create_quiz(self, *, title="Standalone question quiz", course=None):
        return Quiz.objects.create(
            level="COURSE",
            title=title,
            course=course or self.course,
            created_by=self.creator,
            updated_by=self.creator,
        )

    def _question_payload(self, quiz, **overrides):
        payload = {
            "quiz": str(quiz.id),
            "question_text": "Which keyword defines a Python function?",
            "question_type": "MULTIPLE_CHOICE",
            "points": 5,
            "order": 1,
            "options": [
                {"option_text": "func", "is_correct": False, "order": 1},
                {"option_text": "def", "is_correct": True, "order": 2},
            ],
        }
        payload.update(overrides)
        return payload

    def test_creator_can_create_quiz_with_nested_questions(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            "/api/v1/quizzes/", self._quiz_payload(), format="json"
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            msg=f"Unexpected errors: {response.data}",
        )
        quiz = Quiz.objects.get(id=response.data["id"])
        self.assertEqual(quiz.level, "COURSE")
        self.assertEqual(quiz.course_id, self.course.id)
        self.assertEqual(quiz.questions.count(), 2)
        mc = quiz.questions.get(question_type="MULTIPLE_CHOICE")
        self.assertEqual(mc.options.count(), 2)
        self.assertTrue(mc.options.filter(option_text="4", is_correct=True).exists())
        self.assertEqual(quiz.created_by, self.creator)
        self.assertFalse(
            quiz.questions.exclude(
                created_by=self.creator, updated_by=self.creator
            ).exists()
        )

    def test_creator_can_create_question_with_nested_options(self):
        quiz = self._create_quiz()
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/questions/", self._question_payload(quiz), format="json"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            msg=f"Unexpected errors: {response.data}",
        )
        self.assertEqual(str(response.data["quiz"]), str(quiz.id))
        self.assertEqual(response.data["options"][1]["option_text"], "def")
        question = Question.objects.get(pk=response.data["id"])
        self.assertEqual(question.created_by, self.creator)
        self.assertEqual(question.updated_by, self.creator)

    def test_question_create_without_quiz_returns_structured_400(self):
        quiz = self._create_quiz()
        payload = self._question_payload(quiz)
        payload.pop("quiz")
        self.client.force_authenticate(self.creator)

        response = self.client.post("/api/v1/questions/", payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("errors", response.data)
        self.assertTrue(
            any(error["field_name"] == "quiz" for error in response.data["errors"])
        )

    @override_settings(DEBUG=True)
    def test_unexpected_question_error_returns_safe_json_in_debug(self):
        quiz = self._create_quiz()
        self.client.force_authenticate(self.creator)
        self.client.raise_request_exception = False

        with patch.object(
            QuestionSerializer,
            "create",
            side_effect=TypeError("sensitive implementation detail"),
        ):
            response = self.client.post(
                "/api/v1/questions/", self._question_payload(quiz), format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(response.data["errors"][0]["code"], "error")
        self.assertNotIn("sensitive implementation detail", str(response.data))

    def test_question_create_rejects_invalid_options_with_structured_400(self):
        quiz = self._create_quiz()
        payload = self._question_payload(quiz)
        payload["options"][0]["is_correct"] = True
        self.client.force_authenticate(self.creator)

        response = self.client.post("/api/v1/questions/", payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            any(
                error["field_name"] == "options"
                and "exactly one correct option" in error["message"]
                for error in response.data["errors"]
            )
        )
        self.assertFalse(Question.objects.filter(quiz=quiz).exists())

    def test_question_create_rejects_duplicate_option_order(self):
        quiz = self._create_quiz()
        payload = self._question_payload(quiz)
        payload["options"][1]["order"] = 1
        self.client.force_authenticate(self.creator)

        response = self.client.post("/api/v1/questions/", payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            any(error["field_name"] == "options" for error in response.data["errors"])
        )

    def test_question_list_applies_quiz_and_type_filters(self):
        quiz = self._create_quiz()
        other_quiz = self._create_quiz(title="Other quiz")
        included = Question.objects.create(
            quiz=quiz,
            question_text="Included",
            question_type="ESSAY",
            order=1,
        )
        Question.objects.create(
            quiz=quiz,
            question_text="Wrong type",
            question_type="MULTIPLE_CHOICE",
            order=2,
        )
        Question.objects.create(
            quiz=other_quiz,
            question_text="Wrong quiz",
            question_type="ESSAY",
            order=1,
        )
        self.client.force_authenticate(self.creator)

        response = self.client.get(
            "/api/v1/questions/",
            {"quiz": str(quiz.id), "question_type": "ESSAY"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("paginator", response.data["data"])
        self.assertEqual(
            [item["id"] for item in response.data["data"]["results"]],
            [str(included.id)],
        )

    def test_question_retrieve_and_delete_routes(self):
        quiz = self._create_quiz()
        question = Question.objects.create(
            quiz=quiz,
            question_text="Remove me",
            question_type="ESSAY",
            order=1,
        )
        self.client.force_authenticate(self.creator)

        detail_response = self.client.get(f"/api/v1/questions/{question.id}/")
        delete_response = self.client.delete(f"/api/v1/questions/{question.id}/")

        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data["question_text"], "Remove me")
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Question.objects.filter(pk=question.pk).exists())

    def test_question_update_persists_updating_user(self):
        quiz = self._create_quiz()
        question = Question.objects.create(
            quiz=quiz,
            question_text="Before",
            question_type="ESSAY",
            order=1,
            created_by=self.creator,
            updated_by=self.creator,
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            f"/api/v1/questions/{question.id}/",
            {"question_text": "After"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        question.refresh_from_db()
        self.assertEqual(question.question_text, "After")
        self.assertEqual(question.updated_by, self.admin)

    def test_creator_cannot_access_another_creators_question_or_quiz(self):
        other_creator = make_user(role=UserRole.COURSE_CREATOR)
        other_course = Course.objects.create(
            title="Another creator's course",
            creator=other_creator,
            category=self.category,
            description="A private course.",
            terms_accepted_at=timezone.now(),
        )
        other_quiz = self._create_quiz(title="Private quiz", course=other_course)
        other_quiz.created_by = other_creator
        other_quiz.updated_by = other_creator
        other_quiz.save(update_fields=["created_by", "updated_by"])
        other_question = Question.objects.create(
            quiz=other_quiz,
            question_text="Private question",
            question_type="ESSAY",
            order=1,
            created_by=other_creator,
            updated_by=other_creator,
        )
        self.client.force_authenticate(self.creator)

        list_response = self.client.get("/api/v1/questions/")
        detail_response = self.client.get(f"/api/v1/questions/{other_question.id}/")
        create_response = self.client.post(
            "/api/v1/questions/",
            self._question_payload(other_quiz, order=2),
            format="json",
        )
        quiz_response = self.client.get(f"/api/v1/quizzes/{other_quiz.id}/")

        listed_ids = {item["id"] for item in list_response.data["data"]["results"]}
        self.assertNotIn(str(other_question.id), listed_ids)
        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(create_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(quiz_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_nested_quiz_rejects_duplicate_question_order_without_partial_write(self):
        payload = self._quiz_payload(title="Atomic failure")
        payload["questions"][1]["order"] = 1
        self.client.force_authenticate(self.creator)

        response = self.client.post("/api/v1/quizzes/", payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Quiz.objects.filter(title="Atomic failure").exists())

    def test_level_parent_mismatch_rejected(self):
        self.client.force_authenticate(self.creator)
        payload = self._quiz_payload(level="LESSON")
        response = self.client.post("/api/v1/quizzes/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_multiple_choice_requires_options(self):
        self.client.force_authenticate(self.creator)
        payload = self._quiz_payload()
        payload["questions"][0]["options"] = []
        response = self.client.post("/api/v1/quizzes/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_essay_cannot_have_options(self):
        self.client.force_authenticate(self.creator)
        payload = self._quiz_payload()
        payload["questions"][1]["options"] = [
            {"option_text": "nope", "is_correct": True, "order": 1}
        ]
        response = self.client.post("/api/v1/quizzes/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reviewer_cannot_create_quiz(self):
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            "/api/v1/quizzes/", self._quiz_payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_filter_by_level(self):
        Quiz.objects.create(level="MODULE", title="Module quiz", module=self.module)
        Quiz.objects.create(level="COURSE", title="Course quiz", course=self.course)
        self.client.force_authenticate(self.creator)
        response = self.client.get("/api/v1/quizzes/", {"level": "MODULE"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Module quiz")

    def test_update_replaces_option_set(self):
        quiz = Quiz.objects.create(
            level="LESSON", title="Lesson quiz", lesson=self.lesson
        )
        question = Question.objects.create(
            quiz=quiz,
            question_text="Pick one",
            question_type="MULTIPLE_CHOICE",
        )
        QuestionOption.objects.create(
            question=question, option_text="Old", is_correct=True
        )
        self.client.force_authenticate(self.creator)
        response = self.client.patch(
            f"/api/v1/questions/{question.id}/",
            {
                "options": [
                    {"option_text": "New A", "is_correct": False, "order": 1},
                    {"option_text": "New B", "is_correct": True, "order": 2},
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        texts = list(question.options.values_list("option_text", flat=True))
        self.assertEqual(texts, ["New A", "New B"])

    def test_exactly_one_parent_constraint_enforced(self):
        with self.assertRaises(Exception):
            Quiz.objects.create(
                level="COURSE",
                title="Bad quiz",
                course=self.course,
                module=self.module,
            )


class QuizConstraintTests(APITestCase):
    """DB-level constraints added for the target schema parity."""

    def setUp(self):
        from api.courses.tests.factories import make_category, make_user
        from api.courses.models import Course
        from django.utils import timezone

        self.creator = make_user()
        self.category = make_category()
        self.course = Course.objects.create(
            title="Constraint course",
            creator=self.creator,
            category=self.category,
            description="A course for constraint tests.",
            terms_accepted_at=timezone.now(),
        )
        self.quiz = Quiz.objects.create(level="COURSE", title="Q", course=self.course)

    def _question(self, order=1):
        return Question.objects.create(
            quiz=self.quiz,
            question_text="Question?",
            question_type="MULTIPLE_CHOICE",
            points=5,
            order=order,
        )

    def test_duplicate_question_order_rejected(self):
        self._question(order=1)
        with self.assertRaises(Exception):
            self._question(order=1)

    def test_second_correct_option_rejected(self):
        question = self._question()
        QuestionOption.objects.create(
            question=question, option_text="A", is_correct=True, order=1
        )
        with self.assertRaises(Exception):
            QuestionOption.objects.create(
                question=question, option_text="B", is_correct=True, order=2
            )

    def test_single_correct_and_wrong_options_coexist(self):
        question = self._question()
        QuestionOption.objects.create(
            question=question, option_text="A", is_correct=True, order=1
        )
        QuestionOption.objects.create(
            question=question, option_text="B", is_correct=False, order=2
        )
        QuestionOption.objects.create(
            question=question, option_text="C", is_correct=False, order=3
        )
        self.assertEqual(question.options.count(), 3)
