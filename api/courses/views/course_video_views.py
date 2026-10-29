from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import APIView

from api.authorization import codenames
from api.authorization.permissions import Perm
from api.courses.models import Course
from api.courses.serializers import CourseDetailSerializer
from api.courses.serializers.course_video_serializer import (
    CourseVideoDecisionSerializer,
)
from api.courses.services import course_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES
from shared.spectacular.responses import inline_error_response

_COURSE_PK_PARAMETER = OpenApiParameter(
    name="course_pk",
    type=str,
    location=OpenApiParameter.PATH,
    description="UUID of the course.",
)

_COURSE_EXAMPLE = {
    "id": "3f9a2e11-6b7c-4d2a-9e5f-1c8d4a7b2f30",
    "title": "Intro to Python",
    "status": "AWAITING_VIDEO",
    "review_stage": "CONTENT",
    "revision_seat": "",
    "video_provider": "CREATOR",
    "video_attached_at": None,
    "preview_video_url": "",
}


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


def _get_own_course(*, course_pk, user) -> Course:
    """The course, only if `user` created it.

    Ownership is part of the lookup, so a course that belongs to someone else
    is a 404 exactly like one that does not exist, and the endpoint cannot be
    used to discover ids.
    """

    course = Course.objects.filter(pk=course_pk, creator=user).first()
    if course is None:
        raise exceptions.NotFound("Course not found.")
    return course


class CourseVideoDecisionView(APIView):
    """Record who supplies the video once the course's text has been approved."""

    permission_classes = [Perm(codenames.COURSES_CREATE, codenames.COURSES_EDIT)]

    @extend_schema(
        summary="Choose who supplies a course's video",
        description=(
            "After the first review seat approves a course's text, the "
            "creator says whether they will add the video themselves or "
            "leave it to video production.\n\n"
            "Called from the 'Add your video?' prompt shown on a course that "
            "is awaiting its video.\n\n"
            "**Auth:** The course's own creator, holding `courses.create` or "
            "`courses.edit`. Someone else's course is a 404.\n\n"
            "**Prerequisites:** The course must be `AWAITING_VIDEO`, which "
            "only happens with the staged review flow switched on, and must "
            "not be a developer's course (the developer supplies that "
            "video).\n\n"
            "**Important:** The choice can be changed until the video is "
            "submitted. Choosing video production parks the course until "
            "production delivers the video."
        ),
        tags=["Creator — Courses"],
        parameters=[_COURSE_PK_PARAMETER],
        request=CourseVideoDecisionSerializer,
        examples=[
            OpenApiExample(
                name="I will add the video", request_only=True, value={"will_provide": True}
            ),
            OpenApiExample(
                name="Leave it to production",
                request_only=True,
                value={"will_provide": False},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=CourseDetailSerializer,
                description="The course with its video provider recorded.",
                examples=[OpenApiExample(name="Success", value=_COURSE_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            409: inline_error_response(
                description=(
                    "The course is not awaiting its video, or it is a "
                    "developer course whose video the developer supplies."
                ),
                examples=[
                    _error_example(
                        name="Not awaiting video",
                        code="course_not_awaiting_video",
                        message="This course is not waiting for its video.",
                    ),
                    _error_example(
                        name="Developer course",
                        code="video_provider_conflict",
                        message="The developer supplies the video for a developer course.",
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, course_pk):
        course = _get_own_course(course_pk=course_pk, user=request.user)
        serializer = CourseVideoDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = course_service.record_video_decision(
            course=course,
            actor=request.user,
            will_provide=serializer.validated_data["will_provide"],
        )
        return Response(CourseDetailSerializer(course, context={"request": request}).data)


class CourseSubmitVideoView(APIView):
    """Send a course's finished video to the video review seat."""

    permission_classes = [Perm(codenames.COURSES_CREATE, codenames.COURSES_EDIT)]

    @extend_schema(
        summary="Submit a course's video for review",
        description=(
            "Attaches the course's video and sends it to the video review "
            "seat. The preview video and a media reference on every video "
            "lesson must be in place, and every text rule is re-checked.\n\n"
            "Called from the 'Submit video' action once the creator has "
            "added the preview video and each lesson's video.\n\n"
            "**Auth:** The course's own creator, holding `courses.create` or "
            "`courses.edit`. Someone else's course is a 404.\n\n"
            "**Prerequisites:** The course must be `AWAITING_VIDEO` and the "
            "creator must have chosen to add the video themselves "
            "(`video-decision`).\n\n"
            "**Important:** Validation failures come back together under "
            "`structural_standards`. Only the video seat is reset: the first "
            "seat keeps its approval, so the text is not reviewed again. A "
            "course whose video production supplies cannot be submitted "
            "here. This takes no body."
        ),
        tags=["Creator — Courses"],
        parameters=[_COURSE_PK_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(
                response=CourseDetailSerializer,
                description="The course, now waiting at the video review seat.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            **_COURSE_EXAMPLE,
                            "status": "SUBMITTED",
                            "review_stage": "SECOND_REVIEW",
                            "video_attached_at": "2026-10-08T09:30:11.204Z",
                            "preview_video_url": "https://example.com/preview.mp4",
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                description="The course fails the structural standards.",
                examples=[
                    OpenApiExample(
                        name="Fails structural standards",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "['Course must have a preview video "
                                        "before submission (BR-015).']"
                                    ),
                                    "field_name": "structural_standards",
                                }
                            ]
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            409: inline_error_response(
                description=(
                    "The course is not awaiting its video, or this caller is "
                    "not the one who supplies it."
                ),
                examples=[
                    _error_example(
                        name="Not awaiting video",
                        code="course_not_awaiting_video",
                        message="This course is not waiting for its video.",
                    ),
                    _error_example(
                        name="Production supplies it",
                        code="video_provider_conflict",
                        message="The production engine supplies the video for this course.",
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, course_pk):
        course = _get_own_course(course_pk=course_pk, user=request.user)
        course = course_service.submit_video(course=course, actor=request.user)
        return Response(CourseDetailSerializer(course, context={"request": request}).data)
