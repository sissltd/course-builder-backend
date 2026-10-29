from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4


from api.courses.models import Course, Lesson
from api.courses.tests.factories import make_user
from api.mie.enums import WebhookEventType
from api.mie.models import StudioReviewReceipt, WebhookEvent
from api.mie.services.submission_admin_service import decide_submission
from api.mie.tests.factories import make_approved_developer
from api.mie.tests.test_course_push_api import CoursePushTestCase
from api.mie.tests.test_staged_course_video_api import _text_body, _review
from api.platform.services import platform_settings_service
from api.users.enums import UserRole
from shared.services.storage_service import StorageService

REVIEW = "/api/v1/mie/v1/studio/reviews/"
UPLOAD = "/api/v1/mie/v1/studio/uploads/"


class StudioReviewTests(CoursePushTestCase):
    def setUp(self):
        super().setUp()
        platform_settings_service.update_settings(staged_review_flow_enabled=True)
        self.idea = decide_submission(
            actor=make_user(role=UserRole.SUPER_ADMIN),
            submission=self.idea,
            approve=True,
        )
        self.content = _text_body(
            title=self.idea.title, category=self.category, version=self.version
        )
        self.source = [
            {
                "id": f"module-{mi}",
                "order": mi,
                "lessons": [
                    {"id": f"lesson-{mi}-{li}", "order": li} for li in range(1, 4)
                ],
            }
            for mi in range(1, 5)
        ]
        self.envelope = {
            "schema_version": "studio.review.v1",
            "submission_id": str(self.idea.id),
            "course_id": str(self.idea.reserved_course_id),
            "revision": 1,
            "kind": "text",
            "content_hash": "a" * 64,
            "content": self.content,
            "source_map": self.source,
        }

    def send(self, body=None, key=None, idem="delivery-1"):
        return self.client.post(
            REVIEW,
            body or self.envelope,
            format="json",
            HTTP_X_MIE_API_KEY=key or self.key,
            HTTP_IDEMPOTENCY_KEY=idem,
        )

    def test_text_receipt_replays_without_rebuilding_or_duplicate_events(self):
        response = self.send()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["course_id"], str(self.idea.reserved_course_id))
        self.assertFalse(response.data["media_persisted"])
        count = WebhookEvent.objects.count()
        ids = list(Lesson.objects.values_list("id", flat=True))
        repeated = self.send()
        self.assertEqual(repeated.data, response.data)
        self.assertEqual(WebhookEvent.objects.count(), count)
        self.assertEqual(list(Lesson.objects.values_list("id", flat=True)), ids)
        changed = deepcopy(self.envelope)
        changed["content"]["description"] = "different " * 150
        self.assertEqual(self.send(changed).status_code, 409)
        self.assertEqual(self.send(idem="different-key").status_code, 409)

    def test_other_developer_cannot_read_receipt_or_use_reserved_identity(self):
        self.assertEqual(self.send().status_code, 200)
        _, other_key = make_approved_developer()
        self.assertEqual(self.send(key=other_key).status_code, 404)
        self.assertEqual(
            self.client.post(REVIEW, self.envelope, format="json").status_code, 401
        )

    def test_invalid_identity_mapping_and_structure_leave_no_receipt_or_course(self):
        for change in ("course", "source", "structure", "video"):
            body = deepcopy(self.envelope)
            if change == "course":
                body["course_id"] = str(uuid4())
            elif change == "source":
                body["source_map"][0]["lessons"][1]["id"] = "lesson-1-1"
            elif change == "structure":
                body["content"]["final_assessment"]["questions"] = []
            else:
                body["content"]["preview_video_url"] = "https://example.com/preview.mp4"
            self.assertIn(self.send(body).status_code, (400, 409))
            self.assertFalse(Course.objects.exists())
            self.assertFalse(StudioReviewReceipt.objects.exists())

    def test_staged_review_must_be_enabled(self):
        platform_settings_service.update_settings(staged_review_flow_enabled=False)
        self.assertEqual(self.send().status_code, 409)
        self.assertFalse(Course.objects.exists())

    def test_malformed_curriculum_is_a_validation_error(self):
        for modules in (
            ["invalid"],
            [{"title": "module", "lessons": ["invalid"]}],
            None,
        ):
            body = deepcopy(self.envelope)
            body["content"]["modules"] = modules
            self.assertEqual(self.send(body).status_code, 400)

    def test_revision_webhook_names_original_studio_lesson(self):
        self.assertEqual(self.send().status_code, 200)
        course = Course.objects.get(pk=self.idea.reserved_course_id)
        lesson = Lesson.objects.get(module__course=course, module__order=1, order=2)
        self.client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))
        self.assertEqual(
            self.client.post(
                f"/api/v1/review-queue/{course.id}/claim/", format="json"
            ).status_code,
            200,
        )
        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/reject/",
            {
                "feedback": {"summary": "Clarify the worked example."},
                "flags": [
                    {
                        "lesson_id": str(lesson.id),
                        "flag_type": "CONTENT_CLARITY",
                        "title": "Clarify example",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.client.force_authenticate(None)
        event = WebhookEvent.objects.filter(
            event_type=WebhookEventType.COURSE_REVISION_REQUESTED
        ).latest("created_datetime")
        feedback = event.payload["submission"]["course"]["revision_feedback"]
        self.assertEqual(feedback["targets"], [{"lesson_id": "lesson-1-2"}])
        self.assertEqual(feedback["stage"], "text")
        self.assertEqual(feedback["reason"], "Clarify the worked example.")
        repaired = deepcopy(self.envelope)
        repaired["revision"] = 2
        repaired["content_hash"] = "c" * 64
        repaired["content"]["modules"][0]["lessons"][1]["script"] = "clarified " * 600
        self.assertEqual(self.send(repaired, idem="repair-2").status_code, 200)

    def _approve(self):
        self.assertEqual(self.send().status_code, 200)
        course = Course.objects.get(pk=self.idea.reserved_course_id)
        _review(self.client, course, make_user(role=UserRole.STAFF_WRITER))
        event = WebhookEvent.objects.filter(
            event_type=WebhookEventType.COURSE_TEXT_APPROVED
        ).latest("created_datetime")
        self.assertEqual(
            event.payload["submission"]["course"]["video_assignment"]["producer"],
            "studio",
        )
        return course

    def _upload(self, purpose, filename):
        asset = str(uuid4())
        body = {
            "submission_id": str(self.idea.id),
            "asset_id": asset,
            "filename": filename,
            "purpose": purpose,
            "size": 1024,
            "sha256": "d" * 64,
            "content_type": "application/x-subrip"
            if purpose == "SUBTITLE"
            else "video/mp4",
        }
        if purpose != "SUBTITLE":
            body.update(width=1280, height=720, codec="h264", duration_seconds=90)
        file_key = f"uploads/courses/{asset}/{filename}"
        grant = {
            "upload_url": "https://storage.example.test/signed",
            "upload_headers": {},
            "file_url": "https://storage.example.test/read",
            "file_key": file_key,
            "expires_in": 900,
        }
        with patch.object(StorageService, "request_upload", return_value=grant):
            response = self.client.post(
                UPLOAD, body, format="json", HTTP_X_MIE_API_KEY=self.key
            )
        self.assertEqual(response.status_code, 200, response.data)
        with (
            patch.object(StorageService, "file_exists", return_value=True),
            patch.object(StorageService, "verify_file", return_value=True),
            patch.object(StorageService, "request_upload") as presign,
        ):
            repeated = self.client.post(
                UPLOAD, body, format="json", HTTP_X_MIE_API_KEY=self.key
            )
            self.assertTrue(repeated.data["persisted"])
            presign.assert_not_called()
        return response.data["media_url"]

    def test_video_requires_owned_persisted_media_and_keeps_transcripts(self):
        course = self._approve()
        preview = self._upload("COURSE_PREVIEW_VIDEO", "preview.mp4")
        video = self._upload("LESSON_VIDEO", "lesson.mp4")
        captions = self._upload("SUBTITLE", "captions.srt")
        body = {
            **self.envelope,
            "kind": "video",
            "content_hash": "b" * 64,
            "content": {
                "preview_video_url": preview,
                "lessons": [
                    {
                        "module_order": mi,
                        "lesson_order": li,
                        "video_url": video,
                        "captions_url": captions,
                        "transcript": f"Narration {mi}-{li}",
                    }
                    for mi in range(1, 5)
                    for li in range(1, 4)
                ],
            },
        }
        with patch.object(StorageService, "verify_file", return_value=False):
            self.assertEqual(self.send(body, idem="video-1").status_code, 400)
        with patch.object(StorageService, "verify_file", return_value=True):
            foreign = deepcopy(body)
            foreign["content"]["lessons"][0]["captions_url"] = (
                StorageService.public_url("uploads/courses/foreign.srt")
            )
            self.assertEqual(self.send(foreign, idem="video-1").status_code, 400)
            response = self.send(body, idem="video-1")
            self.assertEqual(response.status_code, 200, response.data)
            self.assertTrue(response.data["media_persisted"])
            self.assertEqual(self.send(body, idem="video-1").data, response.data)
        course.refresh_from_db()
        self.assertEqual(course.review_stage, "SECOND_REVIEW")
        self.assertEqual(
            Lesson.objects.filter(module__course=course, content_type="VIDEO").count(),
            12,
        )
        self.assertEqual(len(StudioReviewReceipt.objects.get(kind="video").media), 13)
        lesson = Lesson.objects.get(module__course=course, module__order=1, order=1)
        self.assertEqual(lesson.video_transcript, "Narration 1-1")
        self.assertEqual(lesson.video_script_file, captions)

    def test_upload_is_denied_before_platform_assignment(self):
        response = self.client.post(
            UPLOAD,
            {
                "submission_id": str(self.idea.id),
                "asset_id": str(uuid4()),
                "filename": "captions.srt",
                "purpose": "SUBTITLE",
                "size": 10,
                "sha256": "d" * 64,
                "content_type": "application/x-subrip",
            },
            format="json",
            HTTP_X_MIE_API_KEY=self.key,
        )
        self.assertEqual(response.status_code, 409)

    def test_invalid_video_transcript_and_captions_are_rejected(self):
        from api.mie.serializers.studio_review_serializer import StudioVideoSerializer

        valid = {
            "preview_video_url": "https://example.com/preview.mp4",
            "lessons": [
                {
                    "module_order": 1,
                    "lesson_order": 1,
                    "video_url": "https://example.com/video.mp4",
                    "captions_url": "https://example.com/captions.srt",
                    "transcript": "Narration",
                }
            ],
        }
        for field, value in (
            ("transcript", {}),
            ("transcript", "x" * 100001),
            ("captions_url", "invalid"),
            ("video_url", ""),
        ):
            body = deepcopy(valid)
            body["lessons"][0][field] = value
            self.assertFalse(StudioVideoSerializer(data=body).is_valid())

    def test_upload_requires_checksum(self):
        response = self.client.post(
            UPLOAD,
            {
                "submission_id": str(self.idea.id),
                "asset_id": str(uuid4()),
                "filename": "captions.srt",
                "purpose": "SUBTITLE",
                "size": 10,
                "content_type": "application/x-subrip",
            },
            format="json",
            HTTP_X_MIE_API_KEY=self.key,
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            any(error["field_name"] == "sha256" for error in response.data["errors"])
        )

    def test_storage_outage_returns_api_error_without_receipt(self):
        from shared.services.storage_service import StorageError

        self.client.raise_request_exception = False

        with patch(
            "api.mie.views.studio_review_views.accept_review",
            side_effect=StorageError("private storage detail"),
        ):
            response = self.send()
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private storage detail", str(response.data))
        self.assertFalse(StudioReviewReceipt.objects.exists())

    def test_separate_video_assignment_carries_explicit_studio_identity(self):
        from api.mie.services.developer_course_admin_service import assign_video

        course = self._approve()
        Course.objects.filter(pk=course.id).update(video_provider="")
        assign_video(
            actor=self.idea.decided_by,
            submission_id=self.idea.id,
            producer="DEVELOPER",
        )
        event = WebhookEvent.objects.filter(
            event_type=WebhookEventType.COURSE_VIDEO_ASSIGNED
        ).latest("created_datetime")
        payload = event.payload["submission"]["course"]
        self.assertEqual(payload["video_producer"], "DEVELOPER")
        self.assertEqual(payload["video_assignment"]["producer"], "studio")
        self.assertTrue(payload["video_assignment"]["assignment_id"])
