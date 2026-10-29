from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework.response import Response
from rest_framework.views import APIView

from api.authorization import codenames
from api.authorization.permissions import Perm
from api.mie.serializers.admin_developer_course_serializer import (
    AdminDeveloperCourseSerializer,
    CourseArchiveSerializer,
    VideoAssignmentSerializer,
)
from api.mie.services import developer_course_admin_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES
from shared.spectacular.responses import inline_error_response

TAG = "Admin — MIE Submissions"

SUBMISSION_ID_PARAMETER = OpenApiParameter(
    name="submission_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    description="The id of the approved idea whose course this is.",
)

COURSE_EXAMPLE = {
    "submission_id": "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11",
    "submission_reference": "SCB-0d1c7b2e-A",
    "course_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "title": "Build a Production-Grade Rust Course",
    "status": "AWAITING_VIDEO",
    "video_producer": "PRODUCTION_ENGINE",
}

NOT_FOUND_EXAMPLES = [
    OpenApiExample(
        "No course pushed yet",
        value={
            "errors": [
                {
                    "type": "client_error",
                    "code": "not_found",
                    "message": "No course has been pushed for this idea yet.",
                    "field_name": None,
                }
            ]
        },
    ),
]


def _error_example(*, name: str, code: str, message: str) -> OpenApiExample:
    return OpenApiExample(
        name=name,
        value={
            "errors": [
                {
                    "type": "client_error",
                    "code": code,
                    "message": message,
                    "field_name": None,
                }
            ]
        },
    )


class MieAdminCourseVideoAssignmentView(APIView):
    """Assign who makes the video of a developer's course."""

    permission_classes = [Perm(codenames.MIE_MANAGE_CONSOLE)]

    @extend_schema(
        summary="Assign the video of a developer's course",
        description=(
            "Hands the video of the course a developer pushed for an idea to "
            "the platform's production engine or to the developer, and tells "
            "the developer with a `COURSE_VIDEO_ASSIGNED` webhook carrying "
            "`video_producer`.\n\n"
            "Called once the course's text has passed the first review seat "
            "and the course is AWAITING_VIDEO. For a developer whose account "
            "requires video assignment this is the step that releases the "
            "course; for any other developer it overrides the default (the "
            "developer sends the video).\n\n"
            "**Auth:** The `mie.manage_console` permission — Super Admin by default.\n\n"
            "**Prerequisites:** The idea has a course and it is "
            "AWAITING_VIDEO, so its video has not been submitted yet.\n\n"
            "**Important:** PRODUCTION_ENGINE queues a production run at "
            "once; DEVELOPER cancels any run in progress. The producer also "
            "sets the price the course is paid at (with or without video). "
            "The assignment can be changed until the video is submitted; "
            "assigning the producer it already has returns 400 and fires no "
            "webhook. The crawler's own courses cannot be handed to its "
            "developer."
        ),
        tags=[TAG],
        parameters=[SUBMISSION_ID_PARAMETER],
        request=VideoAssignmentSerializer,
        examples=[
            OpenApiExample(
                "Give it to the production engine",
                request_only=True,
                value={"video_producer": "PRODUCTION_ENGINE"},
            ),
            OpenApiExample(
                "Give it to the developer",
                request_only=True,
                value={"video_producer": "DEVELOPER"},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=AdminDeveloperCourseSerializer,
                description="The course with its video producer recorded.",
                examples=[OpenApiExample("Assigned", value=COURSE_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            404: inline_error_response(
                description="No such idea, or no course has been pushed for it.",
                examples=NOT_FOUND_EXAMPLES,
            ),
            409: inline_error_response(
                description=(
                    "The course is not waiting for its video, or it is the "
                    "crawler's course and was handed to its developer."
                ),
                examples=[
                    _error_example(
                        name="Not awaiting video",
                        code="course_not_awaiting_video",
                        message="This course is not waiting for its video.",
                    ),
                    _error_example(
                        name="Crawler course",
                        code="video_provider_conflict",
                        message=(
                            "The platform's crawler has no video to send; the "
                            "production engine makes the video for its courses."
                        ),
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, submission_id):
        serializer = VideoAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = developer_course_admin_service.assign_video(
            actor=request.user,
            submission_id=submission_id,
            producer=serializer.validated_data["video_producer"],
        )
        return Response(
            AdminDeveloperCourseSerializer(
                developer_course_admin_service.course_payload(
                    course=course, submission=course.mie_submission
                )
            ).data
        )


class MieAdminCourseArchiveView(APIView):
    """Archive the course a developer pushed for an idea."""

    permission_classes = [Perm(codenames.MIE_MANAGE_CONSOLE)]

    @extend_schema(
        summary="Archive a developer's course",
        description=(
            "Archives the course a developer pushed for an idea, for good, "
            "and tells the developer why with a `COURSE_ARCHIVED` webhook "
            "carrying `archive_reason`.\n\n"
            "Called when a developer's course will not be taken any further "
            "- a final no, where a reviewer's rejection would only send it "
            "back for revision.\n\n"
            "**Auth:** The `mie.manage_console` permission — Super Admin by default.\n\n"
            "**Prerequisites:** The idea has a course that has not passed QA "
            "or been published.\n\n"
            "**Important:** Irreversible. The course leaves every review "
            "queue, any production run on it is cancelled, and the developer "
            "can no longer push it (409 `course_archived`). A payout already "
            "made is not reversed. The idea keeps its status."
        ),
        tags=[TAG],
        parameters=[SUBMISSION_ID_PARAMETER],
        request=CourseArchiveSerializer,
        examples=[
            OpenApiExample(
                "Archive",
                request_only=True,
                value={
                    "reason": (
                        "The course repeats our existing Rust track and two "
                        "revisions did not change its scope."
                    )
                },
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=AdminDeveloperCourseSerializer,
                description="The archived course.",
                examples=[
                    OpenApiExample(
                        "Archived",
                        value={**COURSE_EXAMPLE, "status": "ARCHIVED"},
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            404: inline_error_response(
                description="No such idea, or no course has been pushed for it.",
                examples=NOT_FOUND_EXAMPLES,
            ),
            409: inline_error_response(
                description=(
                    "The course is already archived, or it passed QA or was "
                    "published."
                ),
                examples=[
                    _error_example(
                        name="Published course",
                        code="course_not_archivable",
                        message="A Published course cannot be archived.",
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, submission_id):
        serializer = CourseArchiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = developer_course_admin_service.archive_course(
            actor=request.user,
            submission_id=submission_id,
            reason=serializer.validated_data["reason"],
        )
        return Response(
            AdminDeveloperCourseSerializer(
                developer_course_admin_service.course_payload(
                    course=course, submission=course.mie_submission
                )
            ).data
        )
