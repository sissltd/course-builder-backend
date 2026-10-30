"""Request and response shapes of the Soludesk Studio endpoints.

A delivery's `content` is checked with the push serializer for its kind -
CoursePushSerializer for text, CourseVideoPushSerializer for video - so a
Studio course meets exactly the rules of any pushed course.
"""

from drf_spectacular.utils import OpenApiExample, extend_schema_serializer
from rest_framework import serializers

from api.mie.enums import StudioReviewKind
from api.mie.serializers.course_push_serializer import (
    MAX_PUSH_LESSONS_PER_MODULE,
    MAX_PUSH_MODULES,
    CoursePushSerializer,
)
from api.mie.serializers.course_video_push_serializer import CourseVideoPushSerializer
from api.mie.services import course_push_service, studio_review_service
from api.mie.services.studio_upload_service import MAX_FILES_PER_GRANT
from shared.serializers.storage_serializer import UploadRequestSerializer

SOURCE_ID_MAX_LENGTH = 255


class StudioSourceLessonSerializer(serializers.Serializer):
    id = serializers.CharField(
        max_length=SOURCE_ID_MAX_LENGTH, help_text="Studio's id for the lesson."
    )
    order = serializers.IntegerField(
        min_value=1, help_text="The lesson's `order` in `content`."
    )


class StudioSourceModuleSerializer(serializers.Serializer):
    id = serializers.CharField(
        max_length=SOURCE_ID_MAX_LENGTH, help_text="Studio's id for the module."
    )
    order = serializers.IntegerField(
        min_value=1, help_text="The module's `order` in `content`."
    )
    lessons = StudioSourceLessonSerializer(
        many=True,
        required=False,
        max_length=MAX_PUSH_LESSONS_PER_MODULE,
        help_text="Studio's lesson ids in this module.",
    )


class StudioReviewEnvelopeSerializer(serializers.Serializer):
    """One `studio.review.v1` delivery.

    `content` arrives raw so its hash can be checked against exactly what was
    sent; once it matches, `content` is replaced by the validated push body.
    Expects `staged_review_flow` in the context, as the text push does.
    """

    schema_version = serializers.ChoiceField(
        choices=[studio_review_service.SCHEMA_VERSION],
        help_text=f"Always `{studio_review_service.SCHEMA_VERSION}`.",
    )
    submission_id = serializers.UUIDField(help_text="The approved idea's `id`.")
    course_id = serializers.UUIDField(
        help_text="The `course_id` the idea was given at approval."
    )
    revision = serializers.IntegerField(
        min_value=1,
        help_text=(
            "Your revision number for this idea and kind. Raise it whenever "
            "the content changes; resend the same number only to retry."
        ),
    )
    kind = serializers.ChoiceField(
        choices=StudioReviewKind.choices,
        help_text="`text` for the course text, `video` for its video.",
    )
    content_hash = serializers.RegexField(
        r"^[0-9a-f]{64}$",
        help_text=(
            "Lowercase hex SHA-256 of `content` as canonical JSON: keys "
            "sorted, no whitespace, UTF-8."
        ),
    )
    content = serializers.JSONField(
        help_text=(
            "For `text`, the course-push body of POST "
            "/mie/v1/submissions/{id}/course/. For `video`, the video-push "
            "body of POST /mie/v1/submissions/{id}/course/video/."
        )
    )
    source_map = StudioSourceModuleSerializer(
        many=True,
        required=False,
        max_length=MAX_PUSH_MODULES,
        help_text=(
            "Optional. Studio's module and lesson ids keyed by the orders in "
            "`content`; stored and echoed on the receipt."
        ),
    )

    def validate(self, attrs):
        if (
            studio_review_service.content_hash(attrs["content"])
            != attrs["content_hash"]
        ):
            raise serializers.ValidationError(
                {"content_hash": ["Does not match the SHA-256 of `content`."]}
            )
        if not isinstance(attrs["content"], dict):
            raise serializers.ValidationError({"content": ["Send a JSON object."]})
        body = (
            CoursePushSerializer(data=attrs["content"], context=self.context)
            if attrs["kind"] == StudioReviewKind.TEXT
            else CourseVideoPushSerializer(data=attrs["content"])
        )
        if not body.is_valid():
            raise serializers.ValidationError({"content": body.errors})
        attrs["content"] = body.validated_data
        if attrs["kind"] == StudioReviewKind.TEXT and attrs.get("source_map"):
            _check_source_map(attrs["source_map"], attrs["content"]["modules"])
        return attrs



class StudioTextDeliverySerializer(StudioReviewEnvelopeSerializer):
    """The schema of a `kind: text` delivery, with `content` spelled out as
    the course-push body. Documentation only: requests are validated by
    StudioReviewEnvelopeSerializer, which checks `content` with the same
    CoursePushSerializer."""

    kind = serializers.ChoiceField(
        choices=[StudioReviewKind.TEXT], help_text="`text` for the course text."
    )
    content = CoursePushSerializer(
        help_text=(
            "The whole course as the Course Builder editor writes it: the "
            "course fields, `modules` > `lessons` > `content_blocks`, a quiz "
            "per module, optional lesson quizzes and the `final_assessment`. "
            "Exactly the body of POST /mie/v1/submissions/{id}/course/."
        )
    )


class StudioVideoDeliverySerializer(StudioReviewEnvelopeSerializer):
    """The schema of a `kind: video` delivery, with `content` spelled out as
    the video-push body. Documentation only, like StudioTextDeliverySerializer."""

    kind = serializers.ChoiceField(
        choices=[StudioReviewKind.VIDEO], help_text="`video` for the course video."
    )
    content = CourseVideoPushSerializer(
        help_text=(
            "The preview video and one entry per VIDEO lesson, addressed by "
            "the module and lesson `order`s of the text delivery. Exactly the "
            "body of POST /mie/v1/submissions/{id}/course/video/."
        )
    )

def _check_source_map(source_map: list[dict], modules: list[dict]) -> None:
    """Every module and lesson order in the source map is one `content` has."""

    lessons_by_module = {
        module["order"]: {lesson["order"] for lesson in module["lessons"]}
        for module in modules
    }
    for module in source_map:
        lesson_orders = lessons_by_module.get(module["order"])
        if lesson_orders is None:
            raise serializers.ValidationError(
                {"source_map": [f"content has no module with order {module['order']}."]}
            )
        for lesson in module.get("lessons", []):
            if lesson["order"] not in lesson_orders:
                raise serializers.ValidationError(
                    {
                        "source_map": [
                            f"Module {module['order']} in content has no lesson "
                            f"with order {lesson['order']}."
                        ]
                    }
                )


class StudioMediaSerializer(serializers.Serializer):
    url = serializers.URLField(help_text="A media URL from the video content.")
    asset_id = serializers.CharField(
        allow_null=True, help_text="Your asset_id when the file is in our storage."
    )
    stored = serializers.BooleanField(
        help_text="True only once the file was found in our storage at its declared size."
    )
    source = serializers.ChoiceField(
        choices=["platform", "external"],
        help_text="`platform` for our storage, `external` for a URL you host.",
    )
    size = serializers.IntegerField(
        allow_null=True, help_text="Verified byte size; null for external URLs."
    )


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Text accepted",
            value={
                "receipt_id": "5b1f0c9e-2a7d-4c3e-8f6a-9d0e1b2c3a4f",
                "schema_version": "studio.review.v1",
                "submission_id": "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11",
                "course_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
                "kind": "text",
                "revision": 1,
                "content_hash": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                "status": "accepted",
                "course_status": "IN_REVIEW",
                "source_map": [
                    {
                        "id": "mod_1",
                        "order": 1,
                        "lessons": [{"id": "les_1", "order": 1}],
                    }
                ],
                "media": [],
                "duplicate": False,
                "received_at": "2026-10-29T10:00:00Z",
            },
            response_only=True,
        )
    ]
)
class StudioReviewReceiptSerializer(serializers.Serializer):
    """What a delivery answers with: proof of what the platform took."""

    receipt_id = serializers.UUIDField(help_text="Id of this receipt.")
    schema_version = serializers.CharField(help_text="Envelope version accepted.")
    submission_id = serializers.UUIDField(help_text="The idea the content is for.")
    course_id = serializers.UUIDField(help_text="The course the content went into.")
    kind = serializers.ChoiceField(
        choices=StudioReviewKind.choices, help_text="`text` or `video`."
    )
    revision = serializers.IntegerField(help_text="The revision accepted.")
    content_hash = serializers.CharField(help_text="The hash accepted.")
    status = serializers.CharField(help_text="Always `accepted`.")
    course_status = serializers.CharField(
        help_text="The course's status right after this delivery."
    )
    source_map = serializers.JSONField(help_text="Your source map, as sent.")
    media = StudioMediaSerializer(
        many=True, help_text="Each media URL of a video delivery; empty for text."
    )
    duplicate = serializers.BooleanField(
        help_text=(
            "True when this revision was already accepted with the same hash "
            "and this response is its original receipt."
        )
    )
    received_at = serializers.DateTimeField(help_text="When the content was accepted.")


class StudioUploadFileSerializer(UploadRequestSerializer):
    """One file to upload: the creator presign request, limited to course
    media, plus your own id for the file."""

    folder = None
    asset_id = serializers.RegexField(
        r"^[A-Za-z0-9_.:-]{1,255}$",
        help_text=(
            "Your id for the file, unique per idea. Ask again with the same id "
            "to resume an upload."
        ),
    )
    purpose = serializers.ChoiceField(
        choices=course_push_service.MIE_UPLOAD_PURPOSES,
        help_text="What the file is for; selects its upload rules.",
    )
    size = serializers.IntegerField(
        min_value=1, help_text="Byte count; signed into the PUT."
    )


class StudioUploadBatchSerializer(serializers.Serializer):
    submission_id = serializers.UUIDField(help_text="The approved idea's `id`.")
    files = StudioUploadFileSerializer(
        many=True,
        allow_empty=False,
        max_length=MAX_FILES_PER_GRANT,
        help_text=f"1 to {MAX_FILES_PER_GRANT} files.",
    )

    def validate_files(self, value):
        asset_ids = [item["asset_id"] for item in value]
        if len(asset_ids) != len(set(asset_ids)):
            raise serializers.ValidationError("Each asset_id may appear once.")
        return value


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Upload required",
            value={
                "asset_id": "lesson-1-1-video",
                "status": "upload_required",
                "method": "PUT",
                "upload_url": "https://storage.example.com/uploads/courses/3b9f.mp4?X-Amz-Signature=...",
                "upload_headers": {
                    "Content-Type": "video/mp4",
                    "x-amz-meta-codec": "h264",
                },
                "expires_in": 3600,
                "media_url": "https://storage.example.com/uploads/courses/3b9f.mp4",
                "size": 48000000,
            },
            response_only=True,
        )
    ]
)
class StudioUploadGrantSerializer(serializers.Serializer):
    """Where and how to upload one file - or that it is already stored."""

    asset_id = serializers.CharField(help_text="Your id for the file.")
    status = serializers.ChoiceField(
        choices=["upload_required", "stored"],
        help_text="`stored` when the file is already in storage; no upload needed.",
    )
    method = serializers.CharField(
        allow_null=True, help_text="`PUT`; null when stored."
    )
    upload_url = serializers.URLField(
        allow_null=True,
        help_text=(
            "Signed storage URL. PUT the bytes here with `upload_headers` only - "
            "never your X-MIE-Api-Key. Null when stored."
        ),
    )
    upload_headers = serializers.DictField(
        child=serializers.CharField(),
        allow_null=True,
        help_text="Headers to send unchanged with the PUT; null when stored.",
    )
    expires_in = serializers.IntegerField(
        allow_null=True, help_text="Seconds the URL stays valid; null when stored."
    )
    media_url = serializers.URLField(
        help_text="The durable URL to put in the video delivery's content."
    )
    size = serializers.IntegerField(help_text="The declared byte count.")
