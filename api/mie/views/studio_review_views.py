from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    PolymorphicProxySerializer,
    extend_schema,
)
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers, exceptions

from api.mie.authentication import MieDeveloperAuthentication
from api.mie.permissions import IsMieDeveloper
from api.mie.serializers.studio_review_serializer import (
    StudioReviewReceiptSerializer,
    StudioReviewSerializer,
    StudioTextReviewSchemaSerializer,
    StudioVideoReviewSchemaSerializer,
)
from api.mie.services.documentation_service import studio_delivery_example
from api.mie.services import studio_receipt_service
from api.mie.services.studio_review_service import accept_review
from api.mie.throttling import MieDeveloperRateThrottle
from api.mie.views.studio_delivery import REVIEW_DELIVERY_PARAMETERS
from shared.serializers.storage_serializer import UploadRequestSerializer
from shared.services.storage_service import (
    InvalidFileType,
    FileTooLarge,
    InvalidUploadMetadata,
    StorageError,
)


# The request schema shows `content` in full for each kind; the request itself
# is validated by StudioReviewSerializer and accept_review.
STUDIO_REVIEW_SCHEMA = PolymorphicProxySerializer(
    component_name="StudioReview",
    serializers={
        "text": StudioTextReviewSchemaSerializer,
        "video": StudioVideoReviewSchemaSerializer,
    },
    resource_type_field_name="kind",
)

STUDIO_REVIEW_DESCRIPTION = (
    "Takes one `studio.review.v1` review for an approved idea: the course "
    "text (`kind: text`) first, then its video (`kind: video`) once the text "
    "passes. Answers with a receipt.\n\n"
    "**Auth:** `X-MIE-Api-Key` (or a platform Bearer session token), plus a "
    "required `Idempotency-Key` header.\n\n"
    "**Prerequisites:** The idea is yours and APPROVED, `course_id` is the one "
    "from SUBMISSION_APPROVED, and the platform reviews text before video. "
    "For video, every file was uploaded through POST /mie/v1/studio/uploads/.\n\n"
    "**Important:** `revision` must be higher than the last accepted one of "
    "the kind. `source_map` names every module and lesson; the video review "
    "sends the same map as the accepted text. The same Idempotency-Key and "
    "body returns the original receipt. `content_hash` is the SHA-256 of "
    "`content` as canonical JSON (sorted keys, no whitespace, UTF-8); a "
    "mismatch is 400.\n\n"
    "**`content` for `kind: text`** (the Course Builder editor's shape; live "
    "limits at GET /mie/v1/course-requirements/):\n\n"
    "```\n"
    "course: title, description, category, version, difficulty_level,\n"
    "        thumbnail_url, learning_objectives[], tags[],\n"
    "        duration_hours/minutes/seconds, terms_accepted\n"
    "├─ modules[]: order, title, description, learning_objectives[]\n"
    "│  ├─ lessons[]: order, title, lesson_type TEXT, script,\n"
    "│  │             learning_objectives[], duration_minutes,\n"
    "│  │             lesson_requirement | requirements[]\n"
    "│  │  ├─ content_blocks[]: order, block_type, text_content | media_url\n"
    "│  │  └─ assessment (optional lesson quiz)\n"
    "│  └─ assessment: title, questions[] (the module quiz)\n"
    "└─ final_assessment: title, questions[]\n"
    "question: type (SINGLE_CHOICE|MULTIPLE_CHOICE|ESSAY), question, points,\n"
    "          options[] + correct_index | correct_indices[] (choice),\n"
    "          expected_answer + explanation (essay), explanation (choice, optional)\n"
    "```\n\n"
    "List blocks hold one item per line, with no blank lines and no typed "
    "bullet or number.\n\n"
    "The text carries no video: every lesson TEXT, no video_url, "
    "embedded_link or preview_video_url, no VIDEO or EMBED blocks.\n\n"
    "**`content` for `kind: video`:** `preview_video_url`, and `lessons[]` of "
    "`{module_order, lesson_order, video_url, captions_url, transcript}` for "
    "every lesson of the accepted text.\n\n"
    "Pick the kind in the request body schema to see every field."
)


class MieStudioReviewView(APIView):
    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]
    throttle_scope = "mie_course_push"
    throttle_classes = [MieDeveloperRateThrottle]

    @extend_schema(
        tags=["Developer — MIE Courses"],
        summary="Deliver an exact Studio text or video version for review",
        description=STUDIO_REVIEW_DESCRIPTION,
        parameters=[
            OpenApiParameter(
                name="Idempotency-Key",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description=(
                    "One per review, up to 200 characters. The same key and "
                    "body returns the original receipt; the same key with "
                    "another body is 409."
                ),
            ),
            *REVIEW_DELIVERY_PARAMETERS,
        ],
        request=STUDIO_REVIEW_SCHEMA,
        examples=[
            OpenApiExample(
                "Text review",
                value=studio_delivery_example("text"),
                request_only=True,
            ),
            OpenApiExample(
                "Video review",
                value=studio_delivery_example("video"),
                request_only=True,
            ),
        ],
        responses={200: StudioReviewReceiptSerializer},
    )
    def post(self, request):
        # Before request.data: the Studio payload hash is over the raw bytes.
        studio = studio_receipt_service.read_delivery(request, needs_revision=True)
        serializer = StudioReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            return Response(
                accept_review(
                    developer=request.auth,
                    data=serializer.validated_data,
                    idempotency_key=request.headers.get("Idempotency-Key", ""),
                    studio=studio,
                )
            )
        except StorageError as exc:
            raise exceptions.APIException(
                "Platform media storage is unavailable."
            ) from exc


class StudioUploadSerializer(UploadRequestSerializer):
    submission_id = serializers.UUIDField()
    asset_id = serializers.UUIDField()
    sha256 = serializers.RegexField(r"^[a-f0-9]{64}$")
    size = serializers.IntegerField(min_value=1, max_value=100 * 1024 * 1024)


class MieStudioUploadView(MieStudioReviewView):
    # A video review needs a preview plus a video and captions per lesson, so
    # uploads have their own budget rather than drawing on the reviews'.
    throttle_scope = "mie_studio_upload"

    @extend_schema(
        tags=["Developer — MIE Courses"],
        summary="Reserve or resume a Studio media upload",
        request=StudioUploadSerializer,
        responses={200: dict},
    )
    def post(self, request):
        from api.mie.services.studio_upload_service import request_upload

        serializer = StudioUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        submission_id = data.pop("submission_id")
        asset_id = data.pop("asset_id")
        data.pop("folder", None)
        try:
            return Response(
                request_upload(
                    developer=request.auth,
                    submission_id=submission_id,
                    asset_id=asset_id,
                    metadata=data,
                )
            )
        except (InvalidFileType, FileTooLarge, InvalidUploadMetadata) as exc:
            raise exceptions.ValidationError(str(exc)) from exc
        except StorageError as exc:
            raise exceptions.APIException(
                "Platform media storage is unavailable."
            ) from exc
