from django.db import transaction
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.collaborators.services import collaborator_service
from api.courses.enums import CourseStatus
from api.courses.models import Course, CourseThumbnail
from api.courses.serializers.course_thumbnail_serializer import (
    CourseThumbnailSerializer,
)
from api.users.permissions import IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_COURSE_PK_PARAMETER = OpenApiParameter(
    name="course_pk",
    type=str,
    location=OpenApiParameter.PATH,
    description="UUID of the course.",
)


class CourseThumbnailView(APIView):
    """Get/replace a course's thumbnail.

    GET returns the active thumbnail plus its history (replaced ones are
    kept deactivated). POST replaces the active thumbnail in one atomic
    step: the previous one is deactivated, not deleted, per the target
    schema's is_active design. There is exactly one active thumbnail per
    course, enforced by a partial unique constraint.
    """

    permission_classes = [IsCourseCreatorRole]

    @extend_schema(
        summary="Retrieve a course's thumbnail",
        description=(
            "Returns the course's active thumbnail first, followed by any "
            "deactivated (replaced) ones.\n\n"
            "**Auth:** Course Creator/Writer with access to the course."
        ),
        tags=["Creator — Courses"],
        parameters=[_COURSE_PK_PARAMETER],
        responses={
            200: OpenApiResponse(response=CourseThumbnailSerializer(many=True)),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, course_pk):
        course = self._get_course(course_pk)
        thumbnails = course.thumbnails.all()
        return Response(CourseThumbnailSerializer(thumbnails, many=True).data)

    @extend_schema(
        summary="Set (or replace) a course's thumbnail",
        description=(
            "Sets the course's thumbnail via the 'Add Media' modal's "
            "options: either an uploaded file (`source=UPLOAD` + `file`) or "
            "an external URL (`source` of GOOGLE_DRIVE/YOUTUBE/DROPBOX/LINK "
            "+ `external_url`) - never both. Replacing deactivates the "
            "previous thumbnail rather than deleting it, so history is "
            "kept.\n\n"
            "**Auth:** Course Creator/Writer with manage access to the "
            "course (creator or Admin collaborator); course must be Draft."
        ),
        tags=["Creator — Courses"],
        parameters=[_COURSE_PK_PARAMETER],
        request=CourseThumbnailSerializer,
        responses={
            201: OpenApiResponse(response=CourseThumbnailSerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @transaction.atomic
    def post(self, request, course_pk):
        course = self._get_course(course_pk)
        self._require_manage_access(course)
        if course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "The thumbnail can only be changed while the course is Draft."
            )
        serializer = CourseThumbnailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Deactivate the current active thumbnail(s) - constraint-safe even
        # if a prior bug ever left two active rows.
        course.thumbnails.filter(is_active=True).update(is_active=False)
        thumbnail = CourseThumbnail.objects.create(
            course=course,
            created_by=request.user,
            updated_by=request.user,
            **serializer.validated_data,
        )
        # Keep the denormalized convenience URL in sync for list views.
        course.thumbnail_url = thumbnail.external_url or thumbnail.file
        course.save(update_fields=["thumbnail_url", "updated_datetime"])
        return Response(
            CourseThumbnailSerializer(thumbnail).data,
            status=status.HTTP_201_CREATED,
        )

    def _get_course(self, course_pk) -> Course:
        try:
            return collaborator_service.get_courses_accessible_to(
                self.request.user
            ).get(pk=course_pk)
        except Course.DoesNotExist as exc:
            raise exceptions.NotFound("Course not found.") from exc

    def _require_manage_access(self, course: Course) -> None:
        if not collaborator_service.has_manage_access(
            course=course, user=self.request.user
        ):
            raise exceptions.PermissionDenied(
                "Only the course creator or an Admin collaborator can change the thumbnail."
            )
