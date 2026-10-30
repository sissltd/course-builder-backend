from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
)
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.mie.authentication import MieDeveloperAuthentication
from api.mie.permissions import IsMieDeveloper
from api.mie.serializers.studio_serializer import (
    StudioReviewEnvelopeSerializer,
    StudioReviewReceiptSerializer,
    StudioTextDeliverySerializer,
    StudioVideoDeliverySerializer,
    StudioUploadGrantSerializer,
    StudioUploadBatchSerializer,
)
from api.mie.services import studio_review_service, studio_upload_service
from api.mie.services.documentation_service import studio_delivery_example
from api.mie.throttling import MieDeveloperRateThrottle
from api.mie.views.idempotency import REPLAYED_HEADER
from api.platform.services import platform_settings_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES
from shared.spectacular.responses import inline_error_response

TAG = "Developer — MIE Studio"

SAMPLE_SUBMISSION_ID = "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11"


def _error(code: str, message: str, field_name=None) -> dict:
    return {
        "errors": [
            {
                "type": "client_error" if field_name is None else "validation_error",
                "code": code,
                "message": message,
                "field_name": field_name,
            }
        ]
    }


# The request schema shows `content` in full for each kind; the request
# itself is validated by StudioReviewEnvelopeSerializer.
STUDIO_DELIVERY_SCHEMA = PolymorphicProxySerializer(
    component_name="StudioReviewDelivery",
    serializers={
        "text": StudioTextDeliverySerializer,
        "video": StudioVideoDeliverySerializer,
    },
    resource_type_field_name="kind",
)

CONTENT_STRUCTURE = (
    "**`content` for `kind: text`** (the Course Builder editor's shape; "
    "field rules in each schema below and live limits at GET "
    "/mie/v1/course-requirements/):\n\n"
    "```\n"
    "course: title, description, category, version, difficulty_level,\n"
    "        thumbnail_url, learning_objectives[], tags[],\n"
    "        duration_hours/minutes/seconds, terms_accepted\n"
    "├─ modules[]: order, title, description, learning_objectives[]\n"
    "│  ├─ lessons[]: order, title, lesson_type (TEXT|VIDEO|QUIZ), script,\n"
    "│  │             video_script_file, learning_objectives[],\n"
    "│  │             duration_minutes, lesson_requirement | requirements[]\n"
    "│  │  ├─ content_blocks[]: order, block_type, text_content | media_url\n"
    "│  │  └─ assessment (optional lesson quiz)\n"
    "│  └─ assessment: title, questions[] (the module quiz)\n"
    "└─ final_assessment: title, questions[]\n"
    "question: type (SINGLE_CHOICE|MULTIPLE_CHOICE|ESSAY), question, points,\n"
    "          options[] + correct_index | correct_indices[] (choice),\n"
    "          expected_answer + explanation (essay), explanation (choice, optional)\n"
    "```\n\n"
    "**`content` for `kind: video`:** `preview_video_url`, and `lessons[]` "
    "of `{module_order, lesson_order, video_url | embedded_link}`, one per "
    "VIDEO lesson of the text delivery.\n\n"
    "Pick the kind in the request body schema to see every field. The "
    "examples use every field and block type once."
)


class MieStudioReviewView(APIView):
    """Receive Studio's generated course text or video."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]
    throttle_classes = [MieDeveloperRateThrottle]
    throttle_scope = "mie_course_push"

    @extend_schema(
        summary="Deliver course text or video from Studio",
        description=(
            "Takes one `studio.review.v1` delivery: the course text "
            "(`kind: text`) or its video (`kind: video`) for an approved idea, "
            "and sends it into review. Answers with a receipt.\n\n"
            "Called by Soludesk Studio once its course text is generated, and "
            "again with the video once COURSE_TEXT_APPROVED (or "
            "COURSE_VIDEO_ASSIGNED with video_producer DEVELOPER) arrives.\n\n"
            "**Auth:** Requires a valid MIE developer API key "
            "(`X-MIE-Api-Key`) or a platform Bearer session token.\n\n"
            "**Prerequisites:** The idea is yours and APPROVED. `course_id` is "
            "the one sent with SUBMISSION_APPROVED. For video, every file in "
            "our storage was uploaded through POST /mie/v1/studio/uploads/.\n\n"
            "**Important:** `content` is exactly the body of the course push "
            "(text) or the video push (video), with the same rules and "
            "errors. `content_hash` is the lowercase hex SHA-256 of `content` "
            "as canonical JSON (keys sorted, no whitespace, UTF-8). Resending "
            "the same revision with the same hash returns the original "
            "receipt (200, `duplicate: true`) and changes nothing; changed "
            "content needs a higher revision. A receipt means the content was "
            "taken; `media[].stored` is true only for files verified in our "
            "storage.\n\n" + CONTENT_STRUCTURE
        ),
        tags=[TAG],
        request=STUDIO_DELIVERY_SCHEMA,
        examples=[
            OpenApiExample(
                "Text delivery",
                value=studio_delivery_example("text"),
                request_only=True,
            ),
            OpenApiExample(
                "Video delivery",
                value=studio_delivery_example("video"),
                request_only=True,
            ),
        ],
        responses={
            status.HTTP_201_CREATED: OpenApiResponse(
                StudioReviewReceiptSerializer, description="Content accepted."
            ),
            status.HTTP_200_OK: OpenApiResponse(
                StudioReviewReceiptSerializer,
                description=(
                    "This revision was already accepted with the same hash; "
                    "the original receipt, with `duplicate: true`."
                ),
            ),
            status.HTTP_400_BAD_REQUEST: inline_error_response(
                description=(
                    "The envelope or its content is invalid, the hash does not "
                    "match, or a storage file is missing. Nothing is stored."
                ),
                examples=[
                    OpenApiExample(
                        "Hash mismatch",
                        value=_error(
                            "invalid",
                            "Does not match the SHA-256 of `content`.",
                            "content_hash",
                        ),
                    ),
                    OpenApiExample(
                        "Media not stored",
                        value=_error(
                            "media_not_stored",
                            "Asset lesson-1-1-video is not in storage at its "
                            "declared size of 48000000 bytes. Finish its PUT, "
                            "then deliver again.",
                        ),
                    ),
                ],
            ),
            status.HTTP_404_NOT_FOUND: inline_error_response(
                description="The idea is not yours, or does not exist.",
                examples=[
                    OpenApiExample(
                        "Not yours",
                        value=_error("not_found", "Submission not found."),
                    )
                ],
            ),
            status.HTTP_409_CONFLICT: inline_error_response(
                description="The delivery conflicts with the idea or its course.",
                examples=[
                    OpenApiExample(
                        "Revision conflict",
                        value=_error(
                            "revision_conflict",
                            "This revision was already delivered with different "
                            "content. Send changed content under a new revision "
                            "number.",
                        ),
                    ),
                    OpenApiExample(
                        "Stale revision",
                        value=_error(
                            "stale_revision",
                            "Revision 3 of this text was already accepted; "
                            "revision 2 is older.",
                        ),
                    ),
                    OpenApiExample(
                        "Wrong course id",
                        value=_error(
                            "course_id_mismatch",
                            "course_id is not this idea's course. Use the "
                            "course_id from SUBMISSION_APPROVED or your "
                            "submission queue.",
                        ),
                    ),
                    OpenApiExample(
                        "Idea not approved",
                        value=_error(
                            "idea_not_approved",
                            "A course can only be pushed for an approved idea. "
                            "This idea is REJECTED.",
                        ),
                    ),
                    OpenApiExample(
                        "Course in review",
                        value=_error(
                            "course_in_review",
                            "This idea's course is already IN_REVIEW. It can "
                            "only be replaced after a reviewer sends it back to "
                            "DRAFT or NEEDS_REVISION.",
                        ),
                    ),
                    OpenApiExample(
                        "Not waiting for video",
                        value=_error(
                            "course_not_awaiting_video",
                            "This course is not waiting for its video.",
                        ),
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["rate_limited"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = StudioReviewEnvelopeSerializer(
            data=request.data,
            context={
                "staged_review_flow": (
                    platform_settings_service.is_staged_review_flow_enabled()
                )
            },
        )
        serializer.is_valid(raise_exception=True)
        receipt, duplicate = studio_review_service.deliver(
            developer=request.auth, envelope=serializer.validated_data
        )
        response = Response(
            StudioReviewReceiptSerializer(
                studio_review_service.receipt_payload(
                    receipt=receipt, duplicate=duplicate
                )
            ).data,
            status=status.HTTP_200_OK if duplicate else status.HTTP_201_CREATED,
        )
        if duplicate:
            response[REPLAYED_HEADER] = "true"
        return response


class MieStudioUploadView(APIView):
    """Grant Studio uploads into platform storage for one idea."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]
    throttle_classes = [MieDeveloperRateThrottle]
    throttle_scope = "mie_upload"

    @extend_schema(
        summary="Get upload URLs for Studio course media",
        description=(
            "Returns a signed storage URL per file to upload course media for "
            "one approved idea, and the `media_url` to reference each file by "
            "in a video delivery.\n\n"
            "Called by Soludesk Studio before a video delivery, and again with "
            "the same `asset_id`s to resume after a failed or expired upload.\n\n"
            "**Auth:** Requires a valid MIE developer API key "
            "(`X-MIE-Api-Key`) or a platform Bearer session token.\n\n"
            "**Prerequisites:** The idea is yours and APPROVED.\n\n"
            "**Important:** PUT each file's bytes to its `upload_url` with "
            "`upload_headers` only - never send your X-MIE-Api-Key to "
            "storage. `size`, and for videos `width`, `height`, `codec` (and "
            "`duration_seconds` for the preview), are signed into the upload, "
            "so declare the real values; the rules per purpose are in "
            "GET /mie/v1/course-requirements/. A file already stored comes "
            "back as `status: stored` with no URL. The grant is checked as a "
            "whole: if one file is refused, none is granted."
        ),
        tags=[TAG],
        request=StudioUploadBatchSerializer,
        examples=[
            OpenApiExample(
                "Two lesson videos",
                value={
                    "submission_id": SAMPLE_SUBMISSION_ID,
                    "files": [
                        {
                            "asset_id": f"lesson-1-{lesson}-video",
                            "filename": f"m1l{lesson}.mp4",
                            "content_type": "video/mp4",
                            "purpose": "LESSON_VIDEO",
                            "size": 48000000,
                            "width": 1920,
                            "height": 1080,
                            "codec": "h264",
                        }
                        for lesson in (1, 2)
                    ],
                },
                request_only=True,
            )
        ],
        responses={
            status.HTTP_200_OK: OpenApiResponse(
                StudioUploadGrantSerializer(many=True),
                description="One grant per file, in request order.",
            ),
            status.HTTP_400_BAD_REQUEST: inline_error_response(
                description="A file breaks an upload rule. Nothing is granted.",
                examples=[
                    OpenApiExample(
                        "Wrong codec",
                        value=_error(
                            "invalid",
                            "LESSON_VIDEO requires the H264 codec.",
                            "files",
                        ),
                    )
                ],
            ),
            status.HTTP_404_NOT_FOUND: inline_error_response(
                description="The idea is not yours, or does not exist.",
                examples=[
                    OpenApiExample(
                        "Not yours",
                        value=_error("not_found", "Submission not found."),
                    )
                ],
            ),
            status.HTTP_409_CONFLICT: inline_error_response(
                description="The idea or an asset is in a conflicting state.",
                examples=[
                    OpenApiExample(
                        "Idea not approved",
                        value=_error(
                            "idea_not_approved",
                            "Uploads can only be granted for an approved idea. "
                            "This idea is PENDING_REVIEW.",
                        ),
                    ),
                    OpenApiExample(
                        "Asset already stored",
                        value=_error(
                            "asset_already_stored",
                            "This asset_id is already stored with a different "
                            "size or content type. Use a new asset_id for a "
                            "different file.",
                        ),
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["rate_limited"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = StudioUploadBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        grants = studio_upload_service.grant_uploads(
            developer=request.auth,
            submission_id=serializer.validated_data["submission_id"],
            files=serializer.validated_data["files"],
        )
        return Response(StudioUploadGrantSerializer(grants, many=True).data)
