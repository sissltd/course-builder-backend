from rest_framework import status
from rest_framework.test import APITestCase

from api.collaborators.enums import CollaboratorRole
from api.collaborators.tests.factories import make_collaborator
from api.courses.enums import CourseStatus
from api.courses.models import Lesson, Module
from api.courses.tests.factories import (
    make_category,
    make_draft_course,
    make_user,
)
from api.users.enums import UserRole


class ModuleLessonAssessmentApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.other_creator = make_user(role=UserRole.COURSE_CREATOR)
        self.category = make_category()
        self.course = make_draft_course(creator=self.creator, category=self.category)

    def test_owner_can_create_module(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/",
            {
                "title": "Module 1",
                "order": 1,
                "description": "An introduction to the course tools.",
                "learning_objectives": [
                    "Install the required tools",
                    "Create a first project",
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            response.data["description"], "An introduction to the course tools."
        )
        self.assertEqual(
            response.data["learning_objectives"],
            ["Install the required tools", "Create a first project"],
        )

        module = Module.objects.get(id=response.data["id"])
        self.assertEqual(module.description, "An introduction to the course tools.")
        self.assertEqual(
            module.learning_objectives,
            ["Install the required tools", "Create a first project"],
        )

    def test_non_owner_gets_404_creating_module(self):
        self.client.force_authenticate(self.other_creator)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/",
            {"title": "Module 1", "order": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_collaborator_can_create_module(self):
        # ADMIN-role collaborators get full-course access, including
        # structural changes like adding modules.
        collaborator_user = make_user()
        make_collaborator(
            course=self.course, user=collaborator_user, role=CollaboratorRole.ADMIN
        )
        self.client.force_authenticate(collaborator_user)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/",
            {"title": "Module 1", "order": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_plain_collaborator_cannot_create_module(self):
        # A plain COLLABORATOR only edits assigned modules' content - adding
        # a new module is a structural change reserved for the creator/an
        # ADMIN collaborator (SCCS PRD Section 14).
        collaborator_user = make_user()
        make_collaborator(
            course=self.course,
            user=collaborator_user,
            role=CollaboratorRole.COLLABORATOR,
        )
        self.client.force_authenticate(collaborator_user)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/",
            {"title": "Module 1", "order": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_collaborator_can_create_lesson_under_assigned_module(self):
        module = Module.objects.create(course=self.course, title="M1", order=1)
        collaborator_user = make_user()
        collaborator = make_collaborator(course=self.course, user=collaborator_user)
        collaborator.assigned_modules.set([module])
        self.client.force_authenticate(collaborator_user)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/",
            {
                "title": "L1",
                "order": 1,
                "script": "x",
                "learning_objectives": ["a", "b"],
                "duration_minutes": 10,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_collaborator_cannot_create_lesson_under_unassigned_module(self):
        module = Module.objects.create(course=self.course, title="M1", order=1)
        collaborator_user = make_user()
        make_collaborator(course=self.course, user=collaborator_user)
        self.client.force_authenticate(collaborator_user)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/",
            {
                "title": "L1",
                "order": 1,
                "script": "x",
                "learning_objectives": ["a", "b"],
                "duration_minutes": 10,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_collaborator_still_gets_404_creating_module(self):
        # Regression check: the get_courses_accessible_to refactor must not
        # widen access beyond creator + actual collaborators.
        self.client.force_authenticate(self.other_creator)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/",
            {"title": "Module 1", "order": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_create_module_when_course_not_draft(self):
        self.course.status = CourseStatus.SUBMITTED
        self.course.save()
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/",
            {"title": "Module 1", "order": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_create_lesson_under_module(self):
        module = Module.objects.create(course=self.course, title="M1", order=1)
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/",
            {
                "title": "L1",
                "order": 1,
                "script": "x",
                "learning_objectives": ["a", "b"],
                "duration_minutes": 10,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cannot_create_lesson_when_course_not_draft(self):
        module = Module.objects.create(course=self.course, title="M1", order=1)
        self.course.status = CourseStatus.SUBMITTED
        self.course.save()
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/",
            {"title": "L1", "order": 1, "learning_objectives": ["a", "b"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_lesson_assessment_upsert(self):
        module = Module.objects.create(course=self.course, title="M1", order=1)
        lesson = Lesson.objects.create(module=module, title="L1", order=1)
        self.client.force_authenticate(self.creator)
        url = f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/{lesson.id}/assessment/"

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        payload = {
            "title": "Quiz 1",
            "questions": [
                {
                    "type": "MULTIPLE_CHOICE",
                    "question": "Q1?",
                    "points": 10,
                    "options": [
                        {"text": "A", "explanation": "A is wrong because..."},
                        {"text": "B", "explanation": "B is correct because..."},
                    ],
                    "correct_index": 1,
                }
            ],
        }
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "Quiz 1")

        payload["title"] = "Quiz 1 Updated"
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "Quiz 1 Updated")

    def test_assessment_invalid_question_shape_rejected(self):
        module = Module.objects.create(course=self.course, title="M1", order=1)
        lesson = Lesson.objects.create(module=module, title="L1", order=1)
        self.client.force_authenticate(self.creator)
        url = f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/{lesson.id}/assessment/"

        payload = {
            "title": "Quiz",
            "questions": [
                {
                    "type": "MULTIPLE_CHOICE",
                    "question": "Q1?",
                    "options": [{"text": "only one", "explanation": "n/a"}],
                    "correct_index": 0,
                }
            ],
        }
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_assessment_missing_questions_rejected(self):
        module = Module.objects.create(course=self.course, title="M1", order=1)
        lesson = Lesson.objects.create(module=module, title="L1", order=1)
        self.client.force_authenticate(self.creator)
        url = f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/{lesson.id}/assessment/"

        response = self.client.put(url, {"title": "Quiz"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            any(error["field_name"] == "questions" for error in response.data["errors"])
        )

    def _assessment_url(self, module=None, lesson=None):
        module = module or Module.objects.create(
            course=self.course, title="M1", order=1
        )
        lesson = lesson or Lesson.objects.create(module=module, title="L1", order=1)
        return f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/{lesson.id}/assessment/"

    def test_essay_and_multiple_choice_round_trip(self):
        self.client.force_authenticate(self.creator)
        url = self._assessment_url()

        payload = {
            "title": "Mixed Quiz",
            "questions": [
                {
                    "type": "MULTIPLE_CHOICE",
                    "question": "Which is a valid variable name?",
                    "points": 10,
                    "options": [
                        {"text": "2var", "explanation": "Can't start with a digit."},
                        {"text": "var_2", "explanation": "Correct."},
                    ],
                    "correct_index": 1,
                },
                {
                    "type": "ESSAY",
                    "question": "Explain lists vs tuples.",
                    "points": 15,
                    "expected_answer": "Lists are mutable, tuples are not.",
                    "explanation": "Award credit for identifying the mutability difference.",
                },
            ],
        }
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        questions = response.data["questions"]
        self.assertEqual(questions[0]["type"], "MULTIPLE_CHOICE")
        self.assertEqual(questions[0]["points"], 10)
        self.assertEqual(questions[1]["type"], "ESSAY")
        self.assertEqual(
            questions[1]["explanation"],
            "Award credit for identifying the mutability difference.",
        )
        self.assertEqual(
            questions[1]["expected_answer"],
            "Lists are mutable, tuples are not.",
        )

        summary = response.data["summary"]
        self.assertEqual(summary["total_questions"], 2)
        self.assertEqual(summary["total_points"], 25)
        self.assertEqual(summary["multiple_choice_count"], 1)
        self.assertEqual(summary["essay_count"], 1)

    def test_multiple_choice_option_missing_explanation_rejected(self):
        self.client.force_authenticate(self.creator)
        url = self._assessment_url()

        payload = {
            "title": "Quiz",
            "questions": [
                {
                    "type": "MULTIPLE_CHOICE",
                    "question": "Q1?",
                    "options": [{"text": "A"}, {"text": "B"}],
                    "correct_index": 0,
                }
            ],
        }
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_essay_question_requires_expected_answer_and_explanation(self):
        self.client.force_authenticate(self.creator)
        url = self._assessment_url()

        payload = {
            "title": "Quiz",
            "questions": [{"type": "ESSAY", "question": "Explain X."}],
        }
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        payload["questions"][0]["expected_answer"] = "A reference answer."
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_essay_question_with_options_rejected(self):
        self.client.force_authenticate(self.creator)
        url = self._assessment_url()

        payload = {
            "title": "Quiz",
            "questions": [
                {
                    "type": "ESSAY",
                    "question": "Explain X.",
                    "expected_answer": "A model answer.",
                    "explanation": "Model answer.",
                    "options": [
                        {"text": "A", "explanation": "n/a"},
                        {"text": "B", "explanation": "n/a"},
                    ],
                }
            ],
        }
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_course_builder_quizzes_round_trip_at_every_level(self):
        """Exercise the Quiz Builder's save and preview contract end-to-end.

        The Figma flow lets a creator add a four-option single-choice
        question, select its correct answer, add an essay question, and review
        the computed summary.  The same question format is supported for
        lesson, module, and final-course quizzes.
        """
        self.client.force_authenticate(self.creator)

        module_response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/",
            {"title": "Python basics", "order": 1},
            format="json",
        )
        self.assertEqual(module_response.status_code, status.HTTP_201_CREATED)
        module_id = module_response.data["id"]

        lesson_response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/{module_id}/lessons/",
            {"title": "Variables", "order": 1, "learning_objectives": []},
            format="json",
        )
        self.assertEqual(lesson_response.status_code, status.HTTP_201_CREATED)
        lesson_id = lesson_response.data["id"]

        quiz_questions = [
            {
                "type": "SINGLE_CHOICE",
                "question": "Which variable name is valid?",
                "points": 10,
                "options": [
                    {"text": "2value", "explanation": "It starts with a digit."},
                    {"text": "user-name", "explanation": "Hyphens are not valid."},
                    {"text": "user_name", "explanation": "Underscores are valid."},
                    {"text": "class", "explanation": "It is a reserved keyword."},
                ],
                "correct_index": 2,
            },
            {
                "type": "ESSAY",
                "question": "Explain why descriptive variable names matter.",
                "points": 15,
                "expected_answer": "They make code easier to read and maintain.",
                "explanation": "Award credit for linking names to readability and maintenance.",
            },
        ]
        payload = {"title": "Variables quiz", "questions": quiz_questions}
        lesson_url = (
            f"/api/v1/courses/{self.course.id}/modules/{module_id}/"
            f"lessons/{lesson_id}/assessment/"
        )
        module_url = (
            f"/api/v1/courses/{self.course.id}/modules/{module_id}/assessment/"
        )
        final_url = f"/api/v1/courses/{self.course.id}/final-assessment/"

        for url in (lesson_url, module_url):
            response = self.client.put(url, payload, format="json")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["questions"][0]["correct_index"], 2)
            self.assertEqual(len(response.data["questions"][0]["options"]), 4)
            self.assertEqual(
                response.data["questions"][0]["options"][2]["text"], "user_name"
            )
            self.assertEqual(
                response.data["summary"],
                {
                    "total_questions": 2,
                    "total_points": 25,
                    "single_choice_count": 1,
                    "multiple_choice_count": 0,
                    "essay_count": 1,
                },
            )

        # The final-assessment editor permits incremental saves; its 15-question
        # publication requirement is checked separately when the course submits.
        final_payload = {
            "title": "Python final quiz",
            "questions": [quiz_questions[0] for _ in range(14)] + [quiz_questions[1]],
        }
        response = self.client.put(final_url, final_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["level"], "COURSE")
        self.assertEqual(response.data["summary"]["total_questions"], 15)
        self.assertEqual(response.data["summary"]["total_points"], 155)

        response = self.client.get(final_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["questions"][0]["correct_index"], 2)
        self.assertEqual(
            response.data["questions"][-1]["expected_answer"],
            "They make code easier to read and maintain.",
        )

    def test_multiple_choice_and_legacy_question_shapes_round_trip(self):
        self.client.force_authenticate(self.creator)
        url = self._assessment_url()
        payload = {
            "title": "Choice questions",
            "questions": [
                {
                    "type": "MULTIPLE_CHOICE",
                    "question": "Which are Python collection types?",
                    "points": 8,
                    "options": [
                        {"text": "list", "explanation": "A list is a collection."},
                        {"text": "tuple", "explanation": "A tuple is a collection."},
                        {
                            "text": "function",
                            "explanation": "A function is callable, not a collection.",
                        },
                    ],
                    "correct_indices": [0, 1],
                },
                {
                    # Existing assessments used MULTIPLE_CHOICE with one index.
                    "type": "MULTIPLE_CHOICE",
                    "question": "Which keyword defines a function?",
                    "points": 2,
                    "options": [
                        {"text": "function", "explanation": "Not a keyword."},
                        {"text": "def", "explanation": "Correct."},
                    ],
                    "correct_index": 1,
                },
            ],
        }

        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["questions"][0]["correct_indices"], [0, 1])
        self.assertEqual(response.data["questions"][1]["correct_index"], 1)
        self.assertEqual(
            response.data["summary"],
            {
                "total_questions": 2,
                "total_points": 10,
                "single_choice_count": 0,
                "multiple_choice_count": 2,
                "essay_count": 0,
            },
        )

    def test_multiple_choice_rejects_duplicate_or_invalid_correct_indices(self):
        self.client.force_authenticate(self.creator)
        url = self._assessment_url()
        payload = {
            "title": "Choice questions",
            "questions": [
                {
                    "type": "MULTIPLE_CHOICE",
                    "question": "Which are valid?",
                    "options": [
                        {"text": "A", "explanation": "Explanation A."},
                        {"text": "B", "explanation": "Explanation B."},
                    ],
                    "correct_indices": [0, 0],
                }
            ],
        }

        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        payload["questions"][0]["correct_indices"] = [2]
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_choice_question_rejects_more_than_six_figma_options(self):
        self.client.force_authenticate(self.creator)
        url = self._assessment_url()
        payload = {
            "title": "Too many options",
            "questions": [
                {
                    "type": "SINGLE_CHOICE",
                    "question": "Select one.",
                    "options": [
                        {
                            "text": f"Option {index}",
                            "explanation": f"Explanation {index}.",
                        }
                        for index in range(7)
                    ],
                    "correct_index": 0,
                }
            ],
        }

        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_essay_rejects_all_choice_fields(self):
        self.client.force_authenticate(self.creator)
        url = self._assessment_url()
        payload = {
            "title": "Essay quiz",
            "questions": [
                {
                    "type": "ESSAY",
                    "question": "Explain abstraction.",
                    "points": 12,
                    "expected_answer": "A model answer.",
                    "explanation": "Grading guidance.",
                    "correct_indices": [0],
                }
            ],
        }

        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
