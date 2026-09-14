from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseImportStatus, CourseSourceType
from api.courses.models import CourseImportJob, LessonContentBlock
from api.courses.tests.factories import make_category, make_topic, make_user


CSV_BYTES = b"module,title,content\nBasics,Install Python,Install Python locally.\n"


class CourseImportApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user()
        self.other_creator = make_user(email="other@example.com")
        self.category = make_category()
        self.topic = make_topic(category=self.category)
        self.client.force_authenticate(self.creator)

    def _payload(self, **overrides):
        payload = {
            "file_key": "uploads/course-imports/outline.csv",
            "filename": "outline.csv",
            "content_type": "text/csv",
            "size": len(CSV_BYTES),
            "category": str(self.category.id),
            "topic": str(self.topic.id),
            "title": "Imported CSV Course",
            "description": "Course imported from CSV.",
            "terms_accepted": True,
            "idempotency_key": "import-1",
        }
        payload.update(overrides)
        return payload

    @patch("api.courses.services.course_import_service.StorageService.download_bytes")
    def test_creator_can_start_and_poll_csv_import(self, mock_download):
        mock_download.return_value = CSV_BYTES

        response = self.client.post(
            "/api/v1/course-imports/", self._payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["status"], CourseImportStatus.READY_FOR_REVIEW)
        self.assertEqual(response.data["detected_structure"]["modules"][0]["title"], "Basics")
        self.assertEqual(
            response.data["detected_structure"]["modules"][0]["lessons"][0]["title"],
            "Install Python",
        )

        detail = self.client.get(f"/api/v1/course-imports/{response.data['id']}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["id"], response.data["id"])

    def test_rejects_wrong_file_key_folder(self):
        response = self.client.post(
            "/api/v1/course-imports/",
            self._payload(file_key="uploads/general/outline.csv"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("course-imports", response.data["errors"][0]["message"])

    def test_rejects_extension_content_type_mismatch(self):
        response = self.client.post(
            "/api/v1/course-imports/",
            self._payload(filename="outline.pdf", content_type="text/csv"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["errors"][0]["message"],
            "Filename extension does not match the uploaded document type.",
        )

    @patch("api.courses.services.course_import_service.StorageService.download_bytes")
    def test_idempotency_key_returns_existing_job(self, mock_download):
        mock_download.return_value = CSV_BYTES
        first = self.client.post(
            "/api/v1/course-imports/", self._payload(), format="json"
        )
        second = self.client.post(
            "/api/v1/course-imports/", self._payload(), format="json"
        )

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(CourseImportJob.objects.count(), 1)

    @patch("api.courses.services.course_import_service.StorageService.download_bytes")
    def test_other_creator_cannot_access_job(self, mock_download):
        mock_download.return_value = CSV_BYTES
        created = self.client.post(
            "/api/v1/course-imports/", self._payload(), format="json"
        )

        self.client.force_authenticate(self.other_creator)
        response = self.client.get(f"/api/v1/course-imports/{created.data['id']}/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("api.courses.services.course_import_service.StorageService.download_bytes")
    def test_creator_can_cancel_import(self, mock_download):
        mock_download.return_value = CSV_BYTES
        created = self.client.post(
            "/api/v1/course-imports/", self._payload(), format="json"
        )

        response = self.client.delete(f"/api/v1/course-imports/{created.data['id']}/")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["status"], CourseImportStatus.CANCELLED)

    @patch("api.courses.services.course_import_service.StorageService.download_bytes")
    def test_confirm_creates_course_tree_and_content_blocks(self, mock_download):
        mock_download.return_value = CSV_BYTES
        created = self.client.post(
            "/api/v1/course-imports/", self._payload(), format="json"
        )

        response = self.client.post(
            f"/api/v1/course-imports/{created.data['id']}/confirm/",
            {"structure": created.data["detected_structure"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        job = CourseImportJob.objects.select_related("course").get(pk=created.data["id"])
        course = job.course
        self.assertEqual(course.source_type, CourseSourceType.DOCUMENT_IMPORTED)
        self.assertEqual(course.modules.count(), 1)
        module = course.modules.get()
        self.assertEqual(module.title, "Basics")
        lesson = module.lessons.get()
        self.assertEqual(lesson.title, "Install Python")
        self.assertEqual(lesson.script, "Install Python locally.")
        self.assertEqual(
            LessonContentBlock.objects.get(lesson=lesson).text_content,
            "Install Python locally.",
        )

    @patch("api.courses.services.course_import_service.StorageService.download_bytes")
    def test_unready_failed_and_cancelled_jobs_cannot_confirm(self, mock_download):
        mock_download.return_value = CSV_BYTES
        queued = CourseImportJob.objects.create(
            creator=self.creator,
            category=self.category,
            file_key="uploads/course-imports/queued.csv",
            filename="queued.csv",
            content_type="text/csv",
            size=100,
            title="Queued",
            status=CourseImportStatus.QUEUED,
        )
        response = self.client.post(f"/api/v1/course-imports/{queued.id}/confirm/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        queued.status = CourseImportStatus.CANCELLED
        queued.save(update_fields=["status"])
        response = self.client.post(f"/api/v1/course-imports/{queued.id}/confirm/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["errors"][0]["message"],
            "Cancelled imports cannot be confirmed.",
        )

        queued.status = CourseImportStatus.FAILED
        queued.save(update_fields=["status"])
        response = self.client.post(f"/api/v1/course-imports/{queued.id}/confirm/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["errors"][0]["message"],
            "Failed imports cannot be confirmed. Retry with a new upload.",
        )
