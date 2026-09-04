from uuid import uuid4

from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APITestCase

from api.collaborators.services import collaborator_service
from api.courses.enums import CourseSourceType, LessonContentType, MediaSource
from api.courses.models import (
    CourseThumbnail,
    Lesson,
    LessonContentBlock,
    LessonImage,
    LessonRequirement,
)
from api.courses.tests.factories import make_draft_course, make_user
from api.quizzes.models import Quiz
from api.users.enums import UserRole


class LessonOpenApiContractTests(SimpleTestCase):
    def test_create_lesson_schema_exposes_all_figma_lesson_types(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        operation = schema["paths"][
            "/api/v1/courses/{course_pk}/modules/{module_pk}/lessons/"
        ]["post"]
        json_body = operation["requestBody"]["content"]["application/json"]
        component_name = json_body["schema"]["$ref"].rsplit("/", maxsplit=1)[-1]

        self.assertIn(
            "lesson_type",
            schema["components"]["schemas"][component_name]["properties"],
        )
        self.assertIn(
            "content_type",
            schema["components"]["schemas"][component_name]["properties"],
        )
        self.assertIn(
            "requirements",
            schema["components"]["schemas"][component_name]["properties"],
        )
        self.assertIn(
            "lesson_requirement",
            schema["components"]["schemas"][component_name]["properties"],
        )
        self.assertTrue(
            schema["components"]["schemas"][component_name]["properties"][
                "content_type"
            ]["deprecated"]
        )
        self.assertEqual(
            {
                example["value"]["lesson_type"]
                for example in json_body["examples"].values()
            },
            {"VIDEO", "QUIZ", "TEXT"},
        )
        self.assertTrue(
            all(
                "lesson_requirement" in example["value"]
                for example in json_body["examples"].values()
            )
        )


class LessonSubResourceApiTests(APITestCase):
    """Content blocks, images, and requirements nested under a lesson."""

    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.course = make_draft_course(creator=self.creator)
        self.module = self.course.modules.create(title="M1", order=1)
        self.lesson = self.module.lessons.create(
            title="L1", order=1, script="word " * 600, duration_minutes=20
        )
        self.base = (
            f"/api/v1/courses/{self.course.id}/modules/{self.module.id}/"
            f"lessons/{self.lesson.id}"
        )

    # --- content blocks ---------------------------------------------------

    def test_block_crud_roundtrip(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"{self.base}/content-blocks/",
            {"order": 1, "block_type": "HEADING_1", "text_content": "Overview"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        response = self.client.get(f"{self.base}/content-blocks/")
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["block_type"], "HEADING_1")

        block_id = results[0]["id"]
        response = self.client.patch(
            f"{self.base}/content-blocks/{block_id}/",
            {"text_content": "Overview (revised)"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            LessonContentBlock.objects.get(id=block_id).text_content,
            "Overview (revised)",
        )

        response = self.client.delete(f"{self.base}/content-blocks/{block_id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_media_block_requires_media_url(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            f"{self.base}/content-blocks/",
            {"order": 1, "block_type": "VIDEO"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_text_block_rejects_media(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            f"{self.base}/content-blocks/",
            {
                "order": 1,
                "block_type": "PARAGRAPH",
                "text_content": "Hello",
                "media_url": "https://example.com/x.png",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_quiz_block_requires_quiz_reference(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            f"{self.base}/content-blocks/",
            {"order": 1, "block_type": "QUIZ"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_quiz_block_can_reference_lesson_quiz(self):
        quiz = Quiz.objects.create(level="LESSON", title="Q", lesson=self.lesson)
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            f"{self.base}/content-blocks/",
            {"order": 1, "block_type": "QUIZ", "quiz": str(quiz.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(response.data["quiz"]), str(quiz.id))

    def test_outsider_cannot_see_blocks(self):
        outsider = make_user()
        self.client.force_authenticate(outsider)
        response = self.client.get(f"{self.base}/content-blocks/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]["results"]), 0)

    # --- images -----------------------------------------------------------

    def test_image_upload_with_caption(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            f"{self.base}/images/",
            {
                "image": "uploads/lessons/pic.png",
                "caption": "A diagram",
                "source_type": "UPLOAD",
                "order": 1,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(LessonImage.objects.filter(caption="A diagram").exists())

    # --- requirements -----------------------------------------------------

    def test_requirement_crud(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            f"{self.base}/requirements/",
            {"text": "Basic Python syntax knowledge", "order": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            LessonRequirement.objects.filter(
                text="Basic Python syntax knowledge"
            ).exists()
        )


class LessonNewFieldsApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(
            email=f"lesson-builder-{uuid4()}@example.com",
            role=UserRole.COURSE_CREATOR,
        )
        self.course = make_draft_course(creator=self.creator)
        self.module = self.course.modules.create(title="M1", order=1)
        self.lesson_base = (
            f"/api/v1/courses/{self.course.id}/modules/{self.module.id}/lessons/"
        )

    def test_create_video_lesson_with_embed(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            self.lesson_base,
            {
                "title": "Intro video",
                "order": 1,
                "content_type": "VIDEO",
                "embedded_link": "https://vimeo.com/123456",
                "duration_minutes": 10,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["lesson_type"], "VIDEO")
        self.assertEqual(response.data["content_type"], "VIDEO")
        lesson = Lesson.objects.get(id=response.data["id"])
        self.assertEqual(lesson.content_type, LessonContentType.VIDEO)
        self.assertEqual(lesson.embedded_link, "https://vimeo.com/123456")

    def test_figma_text_and_quiz_lesson_types_round_trip(self):
        self.client.force_authenticate(self.creator)

        for order, lesson_type in enumerate(("TEXT", "QUIZ"), start=1):
            with self.subTest(lesson_type=lesson_type):
                response = self.client.post(
                    self.lesson_base,
                    {
                        "title": f"{lesson_type.title()} lesson",
                        "order": order,
                        "lesson_type": lesson_type,
                        "duration_minutes": 10,
                    },
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                self.assertEqual(response.data["lesson_type"], lesson_type)
                self.assertEqual(response.data["content_type"], lesson_type)

                detail_response = self.client.get(
                    f"{self.lesson_base}{response.data['id']}/"
                )
                self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
                self.assertEqual(detail_response.data["lesson_type"], lesson_type)
                self.assertEqual(detail_response.data["content_type"], lesson_type)

    def test_legacy_lesson_without_type_defaults_to_text(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            self.lesson_base,
            {"title": "Legacy text lesson", "order": 1},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["lesson_type"], LessonContentType.TEXT)
        self.assertEqual(response.data["content_type"], LessonContentType.TEXT)

    def test_invalid_lesson_type_is_rejected(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            self.lesson_base,
            {
                "title": "Unsupported lesson",
                "order": 1,
                "lesson_type": "AUDIO",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "lesson_type")

    def test_conflicting_lesson_type_aliases_are_rejected(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            self.lesson_base,
            {
                "title": "Conflicting lesson",
                "order": 1,
                "lesson_type": "TEXT",
                "content_type": "QUIZ",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "lesson_type")

    def test_patch_lesson_type_uses_canonical_field(self):
        lesson = self.module.lessons.create(title="Text lesson", order=1)
        self.client.force_authenticate(self.creator)
        response = self.client.patch(
            f"{self.lesson_base}{lesson.id}/",
            {"lesson_type": "QUIZ"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["lesson_type"], LessonContentType.QUIZ)
        self.assertEqual(response.data["content_type"], LessonContentType.QUIZ)
        lesson.refresh_from_db()
        self.assertEqual(lesson.content_type, LessonContentType.QUIZ)

    def test_video_lesson_without_media_rejected(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            self.lesson_base,
            {
                "title": "Broken video lesson",
                "order": 1,
                "lesson_type": "VIDEO",
                "duration_minutes": 10,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_lesson_includes_figma_lesson_requirement(self):
        lesson_requirement = (
            "At the end of this lesson, you will understand computer systems.\n\n"
            "1. The important parts of a computer\n"
            "2. The difference between analogue and digital computers"
        )
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            self.lesson_base,
            {
                "title": "Required knowledge",
                "order": 1,
                "lesson_type": "TEXT",
                "lesson_requirement": lesson_requirement,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["lesson_requirement"], lesson_requirement)
        lesson = Lesson.objects.get(id=response.data["id"])
        self.assertEqual(lesson.requirements.count(), 1)
        self.assertEqual(lesson.requirements.get().text, lesson_requirement)

    def test_patch_requirements_replaces_them_and_omission_preserves_them(self):
        lesson = self.module.lessons.create(title="Text lesson", order=1)
        lesson.requirements.create(text="Old requirement", order=1)
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            f"{self.lesson_base}{lesson.id}/",
            {"title": "Renamed lesson"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(lesson.requirements.count(), 1)

        response = self.client.patch(
            f"{self.lesson_base}{lesson.id}/",
            {"lesson_requirement": "New requirement"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["lesson_requirement"], "New requirement")
        self.assertEqual(list(lesson.requirements.values_list("order", flat=True)), [1])

    def test_empty_lesson_requirement_clears_current_value(self):
        lesson = self.module.lessons.create(title="Text lesson", order=1)
        lesson.requirements.create(text="Old requirement", order=1)
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            f"{self.lesson_base}{lesson.id}/",
            {"lesson_requirement": ""},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["lesson_requirement"], "")
        self.assertFalse(lesson.requirements.exists())

    def test_learning_objective_commas_are_preserved_in_one_array_item(self):
        objective = "Compare variables, constants, and scope"
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            self.lesson_base,
            {
                "title": "Python names",
                "order": 1,
                "lesson_type": "TEXT",
                "learning_objectives": [objective],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["learning_objectives"], [objective])

        detail_response = self.client.get(f"{self.lesson_base}{response.data['id']}/")
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data["learning_objectives"], [objective])

    def test_lesson_read_includes_sub_resources(self):
        lesson = self.module.lessons.create(
            title="L1", order=1, script="x", duration_minutes=5
        )
        lesson.content_blocks.create(
            block_type="PARAGRAPH", order=1, text_content="Hello world"
        )
        lesson.images.create(image="uploads/pic.png", order=1)
        lesson.requirements.create(text="Know the basics", order=1)
        self.client.force_authenticate(self.creator)
        response = self.client.get(f"{self.lesson_base}{lesson.id}/")
        self.assertEqual(len(response.data["content_blocks"]), 1)
        self.assertEqual(len(response.data["images"]), 1)
        self.assertEqual(len(response.data["requirements"]), 1)


class CourseThumbnailApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.course = make_draft_course(creator=self.creator)
        self.url = f"/api/v1/courses/{self.course.id}/thumbnail/"

    def test_set_upload_thumbnail(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            self.url,
            {"source": "UPLOAD", "file": "uploads/thumbs/cover.png"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.course.refresh_from_db()
        self.assertEqual(self.course.thumbnail_url, "uploads/thumbs/cover.png")

    def test_set_external_thumbnail(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            self.url,
            {
                "source": "GOOGLE_DRIVE",
                "external_url": "https://drive.google.com/file/x",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            CourseThumbnail.objects.filter(
                course=self.course, source=MediaSource.GOOGLE_DRIVE
            ).exists()
        )

    def test_upload_without_file_rejected(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(self.url, {"source": "UPLOAD"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_external_with_file_rejected(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            self.url,
            {"source": "LINK", "external_url": "https://x.example", "file": "a.png"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_replace_deactivates_previous(self):
        self.client.force_authenticate(self.creator)
        first = self.client.post(
            self.url,
            {"source": "UPLOAD", "file": "uploads/one.png"},
            format="json",
        )
        second = self.client.post(
            self.url,
            {"source": "UPLOAD", "file": "uploads/two.png"},
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        active = self.course.thumbnails.filter(is_active=True)
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.get().file, "uploads/two.png")

    def test_plain_collaborator_cannot_set_thumbnail(self):
        plain = make_user()
        collaborator_service.get_courses_accessible_to(plain)  # no-op, sanity
        from api.collaborators.models import CourseCollaborator

        CourseCollaborator.objects.create(course=self.course, user=plain)
        self.client.force_authenticate(plain)
        response = self.client.post(
            self.url, {"source": "UPLOAD", "file": "x.png"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CourseNewFieldsApiTests(APITestCase):
    def test_source_type_defaults_and_shown(self):
        creator = make_user(role=UserRole.COURSE_CREATOR)
        course = make_draft_course(creator=creator)
        self.assertEqual(course.source_type, CourseSourceType.CREATOR_UPLOADED)
        self.client.force_authenticate(creator)
        response = self.client.get(f"/api/v1/courses/{course.id}/")
        self.assertIn("source_type", response.data)
        self.assertIn("quality_score", response.data)
        self.assertIsNone(response.data["quality_score"])
