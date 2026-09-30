from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import exceptions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.mie.authentication import MieDeveloperAuthentication
from api.mie.permissions import IsMieDeveloper
from api.mie.serializers.course_push_serializer import (
    CoursePushSerializer,
    DevCourseSerializer,
)
from api.mie.services import course_push_service
from api.mie.services.documentation_service import SAMPLE_COURSE_PUSH
from api.mie.throttling import MieDeveloperRateThrottle
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES
from shared.serializers.storage_serializer import (
    UploadRequestSerializer,
    UploadResponseSerializer,
)
from shared.services.storage_service import (
    COURSE_UPLOAD_RULES,
    FileTooLarge,
    InvalidFileType,
    InvalidUploadMetadata,
    StorageError,
    StorageService,
)

SUBMISSION_ID_PARAMETER = OpenApiParameter(
    name="submission_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    description="The `id` of your approved idea, from the submit response or your queue.",
)


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


class MieCoursePushView(APIView):
    """Push, or check, the course for one of the developer's approved ideas."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]
    throttle_scope = "mie_course_push"

    def get_throttles(self):
        # Only the push is heavy; checking status stays unthrottled like
        # the rest of the read surface.
        if self.request.method == "POST":
            return [MieDeveloperRateThrottle()]
        return []

    @extend_schema(
        summary="Push the course for an approved idea",
        description=(
            "Sends the finished course for one of your approved ideas, in the "
            "same schema the platform's course builder uses, and submits it "
            "for review. From here it follows the normal creator flow: "
            "content review, QA verification, publication.\n\n"
            "Called once your idea has been approved (`SUBMISSION_APPROVED`), "
            "and again whenever a reviewer sends the course back "
            "(`COURSE_REVISION_REQUESTED`).\n\n"
            "**Auth:** Requires a valid MIE developer API key "
            "(`X-MIE-Api-Key`) or a platform Bearer session token.\n\n"
            "**Prerequisites:** The idea is yours and currently APPROVED; "
            "`title` equals the idea's title (trimmed, case-insensitive); "
            "`category` is the idea's category when the idea has one, and an "
            "active category either way; `terms_accepted` is true; every id "
            "comes from `GET /mie/v1/course-requirements/`.\n\n"
            "**Important:** All or nothing. The course is built and "
            "submitted in one transaction; if any rule fails - including the "
            "structural check at submit - nothing is stored and every "
            "failure is returned. The first push creates the course. A later "
            "push is accepted only while a reviewer has sent the course back "
            "to DRAFT, and replaces its content entirely; while the course "
            "is in review or published it returns 409. Media fields take any "
            "HTTPS URL - your own host, a video platform, or our storage via "
            "`POST /mie/v1/uploads/presign/`. Lesson videos are optional; the "
            "course preview video is required. `COURSE_SUBMITTED` fires on "
            "success."
        ),
        tags=["Developer — MIE Courses"],
        parameters=[SUBMISSION_ID_PARAMETER],
        request=CoursePushSerializer,
        examples=[
            OpenApiExample("Course push", value=SAMPLE_COURSE_PUSH, request_only=True),
        ],
        responses={
            status.HTTP_201_CREATED: OpenApiResponse(
                DevCourseSerializer,
                description=(
                    "Course built and submitted for review - on the first "
                    "push and on every accepted revision."
                ),
            ),
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(
                description=(
                    "A field is invalid, the title or category does not match "
                    "the idea, or the course fails the structural check. "
                    "Nothing was stored."
                ),
                examples=[
                    OpenApiExample(
                        "Title does not match the idea",
                        value=_error(
                            "invalid",
                            "The course title must be the approved idea's "
                            "title: 'Build a Production-Grade Rust Course'.",
                            "title",
                        ),
                    ),
                    OpenApiExample(
                        "Structural check failed",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "Course must have a preview video before submission (BR-015).",
                                    "field_name": "structural_standards",
                                },
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "Course must have between 4 and 12 modules (has 1).",
                                    "field_name": "structural_standards",
                                },
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            status.HTTP_409_CONFLICT: OpenApiResponse(
                description=(
                    "The idea is not APPROVED, or its course is already in "
                    "review or published."
                ),
                examples=[
                    OpenApiExample(
                        "Idea not approved",
                        value=_error(
                            "idea_not_approved",
                            "A course can only be pushed for an approved "
                            "idea. This idea is PENDING_REVIEW.",
                        ),
                    ),
                    OpenApiExample(
                        "Course already in review",
                        value=_error(
                            "course_in_review",
                            "This idea's course is already IN_REVIEW. It can "
                            "only be replaced after a reviewer sends it back "
                            "to DRAFT.",
                        ),
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["rate_limited"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, submission_id):
        serializer = CoursePushSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = course_push_service.push_course(
            developer=request.auth,
            submission_id=submission_id,
            data=serializer.validated_data,
        )
        return Response(
            DevCourseSerializer(
                course_push_service.status_payload(
                    course=course, submission=course.mie_submission
                )
            ).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Check the course for an approved idea",
        description=(
            "Returns where the course you pushed for an idea stands in "
            "review, and - while a reviewer has sent it back to DRAFT - the "
            "feedback to act on.\n\n"
            "Called to reconcile after missed webhooks, or before pushing a "
            "revision.\n\n"
            "**Auth:** Requires a valid MIE developer API key "
            "(`X-MIE-Api-Key`) or a platform Bearer session token.\n\n"
            "**Prerequisites:** The idea is yours and a course has been "
            "pushed for it.\n\n"
            "**Important:** 404 both for an idea that is not yours and for "
            "one with no course yet. `revision_feedback` is null unless the "
            "course is DRAFT after a rejection."
        ),
        tags=["Developer — MIE Courses"],
        parameters=[SUBMISSION_ID_PARAMETER],
        responses={
            status.HTTP_200_OK: DevCourseSerializer,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, submission_id):
        return Response(
            DevCourseSerializer(
                course_push_service.course_status(
                    developer=request.auth, submission_id=submission_id
                )
            ).data
        )


class MieUploadRequestSerializer(UploadRequestSerializer):
    """The creator presign request, limited to course media. `folder` is
    derived from `purpose`, so a developer never has to pick one."""

    folder = None
    purpose = serializers.ChoiceField(
        choices=course_push_service.MIE_UPLOAD_PURPOSES,
        help_text=(
            "What the file is for; selects the size, format, resolution, "
            "codec and duration rules - the same ones creators get."
        ),
    )


class MieUploadResponseSerializer(UploadResponseSerializer):
    media_url = serializers.URLField(
        help_text=(
            "The durable URL to put in `preview_video_url`, `thumbnail_url`, "
            "a lesson's `video_url` or a block's `media_url` once the PUT "
            "has succeeded. The object is private; the platform issues "
            "short-lived playback URLs from it."
        )
    )


class MieUploadPresignView(APIView):
    """Presign an upload of course media to platform storage."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]
    throttle_classes = [MieDeveloperRateThrottle]
    throttle_scope = "mie_upload"

    @extend_schema(
        summary="Upload course media to our storage",
        description=(
            "Returns a signed URL to PUT one media file straight to platform "
            "storage, and the `media_url` to reference it by in a course "
            "push.\n\n"
            "Called when you want us to host a video, image or subtitle file "
            "instead of your own host. Optional - any HTTPS URL works in a "
            "push.\n\n"
            "**Auth:** Requires a valid MIE developer API key "
            "(`X-MIE-Api-Key`) or a platform Bearer session token.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** PUT the file bytes to `upload_url` with every "
            "header in `upload_headers`, unchanged, within `expires_in` "
            "seconds. `size`, and for videos and thumbnails `width`, "
            "`height`, `codec` and (preview only) `duration_seconds`, are "
            "signed into the upload - declare the real values. The rules per "
            "purpose are in `GET /mie/v1/course-requirements/`. Store "
            "`media_url`, not `file_url`: `file_url` expires."
        ),
        tags=["Developer — MIE Courses"],
        request=MieUploadRequestSerializer,
        examples=[
            OpenApiExample(
                "Preview video",
                value={
                    "filename": "preview.mp4",
                    "content_type": "video/mp4",
                    "purpose": "COURSE_PREVIEW_VIDEO",
                    "size": 48000000,
                    "width": 1920,
                    "height": 1080,
                    "codec": "h264",
                    "duration_seconds": 90,
                },
                request_only=True,
            ),
        ],
        responses={
            status.HTTP_200_OK: OpenApiResponse(
                MieUploadResponseSerializer,
                description="Signed upload URL and the durable media URL.",
            ),
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(
                description="The file breaks a rule for its purpose.",
                examples=[
                    OpenApiExample(
                        "Wrong codec",
                        value=_error(
                            "invalid",
                            "COURSE_PREVIEW_VIDEO requires the H264 codec.",
                            "non_field_errors",
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
        serializer = MieUploadRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        purpose = serializer.validated_data["purpose"]
        try:
            result = StorageService.request_upload(
                folder=COURSE_UPLOAD_RULES[purpose]["folder"],
                **serializer.validated_data,
            )
        except (InvalidFileType, FileTooLarge, InvalidUploadMetadata) as exc:
            raise exceptions.ValidationError(str(exc)) from exc
        except StorageError as exc:
            raise exceptions.APIException(str(exc)) from exc

        result["media_url"] = StorageService.public_url(result["file_key"])
        return Response(MieUploadResponseSerializer(result).data)


class MieCourseRequirementsView(APIView):
    """Live rules and ids a course push is checked against."""

    authentication_classes = [MieDeveloperAuthentication]
    permission_classes = [IsMieDeveloper]

    @extend_schema(
        summary="Show what a course push needs",
        description=(
            "Returns the live structural rules a pushed course must pass, "
            "the active categories and course versions whose ids a push "
            "references, the allowed choice values, and the upload rules "
            "per media purpose.\n\n"
            "Called before building a push, and whenever a push fails the "
            "structural check - the limits are tunable by platform admins "
            "without notice.\n\n"
            "**Auth:** Requires a valid MIE developer API key "
            "(`X-MIE-Api-Key`) or a platform Bearer session token.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Read it at run time rather than hard-coding it: "
            "these are the same values the platform checks at submit."
        ),
        tags=["Developer — MIE Courses"],
        responses={
            status.HTTP_200_OK: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Current rules, ids and choices.",
                examples=[
                    OpenApiExample(
                        "Requirements",
                        value={
                            "structural_rules": {
                                "modules_per_course": {"min": 4, "max": 12},
                                "lessons_per_module": {"min": 3, "max": 8},
                                "final_assessment_min_questions": 15,
                                "preview_video_required": True,
                            },
                            "categories": [
                                {
                                    "id": "3f2b9c1e-5d4a-4e8f-9b7c-2a1d0e6f4c3b",
                                    "name": "Software Engineering",
                                    "slug": "software-engineering",
                                }
                            ],
                            "course_versions": [
                                {
                                    "id": "b6e2d1a4-9c8f-4a3e-8d2b-1f0e9c8b7a6d",
                                    "label": "v1",
                                }
                            ],
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        return Response(course_push_service.course_requirements())
