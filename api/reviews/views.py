from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from api.collaborators.services import collaborator_service
from api.courses.models import Course
from api.reviews.models import QualityCheckCriterion
from api.reviews.serializers import (
    CourseQualityCheckSerializer,
    QualityCheckCriterionSerializer,
)
from api.reviews.services import quality_check_template_service
from api.authorization import codenames
from api.authorization.permissions import Perm
from api.authorization.services import permission_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_COURSE_PK_PARAMETER = OpenApiParameter(
    name="course_pk",
    type=str,
    location=OpenApiParameter.PATH,
    description="UUID of the course.",
)


@extend_schema_view(
    list=extend_schema(
        summary="List quality-check criteria",
        description=(
            "Returns the checklist template - every criterion grouped by "
            "wizard section. Admins see all criteria (including retired); "
            "creators see only active ones.\n\n"
            "**Auth:** `courses.create` or `courses.manage_quality`; only "
            "`courses.manage_quality` (Admin, Super Admin by default) sees retired criteria."
        ),
        tags=["Quality Check"],
        responses={
            200: OpenApiResponse(response=QualityCheckCriterionSerializer(many=True)),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Add a quality-check criterion",
        description=(
            "Adds a checklist item to a wizard section. Admin-only.\n\n"
            "**Auth:** The `courses.manage_quality` permission — Admin and Super "
            "Admin by default."
        ),
        tags=["Quality Check"],
        request=QualityCheckCriterionSerializer,
        responses={
            201: OpenApiResponse(response=QualityCheckCriterionSerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class QualityCheckCriterionViewSet(ModelViewSet):
    """Admin CRUD for the quality-check checklist template."""

    queryset = QualityCheckCriterion.objects.all()
    serializer_class = QualityCheckCriterionSerializer
    # The template is a small, fixed-ish list (a dozen rows); paginating it
    # would split the checklist the wizard renders in one pass.
    pagination_class = None

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy"}:
            return [Perm(codenames.COURSES_MANAGE_QUALITY)()]
        return [Perm(codenames.COURSES_CREATE, codenames.COURSES_MANAGE_QUALITY)()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return QualityCheckCriterion.objects.none()
        queryset = QualityCheckCriterion.objects.all()
        user = self.request.user
        if not permission_service.user_has_permission(user, codenames.COURSES_MANAGE_QUALITY):
            queryset = queryset.filter(is_active=True)
        return queryset


class CourseQualityCheckView(APIView):
    """A course's quality-check results: GET reads them, POST refreshes.

    POST recomputes every automated criterion from the same structural
    validation the submit gate uses, upserting one result row per active
    criterion. Manual criteria are left untouched by a refresh - the
    creator ticks those off themselves (PATCH is a future slice if the UI
    needs server-side ticking).
    """

    permission_classes = [Perm(codenames.COURSES_CREATE, codenames.COURSES_MANAGE_QUALITY)]

    @extend_schema(
        summary="List a course's quality-check results",
        description=(
            "Returns the course's current result for every active "
            "criterion, grouped by section - the pre-submission checklist "
            "the wizard's Quality Check step renders.\n\n"
            "**Auth:** `courses.create` with access to the course, or "
            "`courses.manage_quality`."
        ),
        tags=["Quality Check"],
        parameters=[_COURSE_PK_PARAMETER],
        responses={
            200: OpenApiResponse(response=CourseQualityCheckSerializer(many=True)),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, course_pk):
        course = self._get_course(course_pk)
        results = (
            course.quality_checks.select_related("criterion")
            .filter(criterion__is_active=True)
            .order_by("criterion__section", "criterion__order_index")
        )
        return Response(CourseQualityCheckSerializer(results, many=True).data)

    @extend_schema(
        summary="Refresh a course's quality checks",
        description=(
            "Re-runs the automated quality checks and upserts one result "
            "per active criterion. Automated criteria (description length, "
            "module/lesson counts, script lengths, preview video, terms, "
            "final assessment) are recomputed; manual criteria keep their "
            "existing state.\n\n"
            "**Auth:** `courses.create` with access to the course, or "
            "`courses.manage_quality`."
        ),
        tags=["Quality Check"],
        parameters=[_COURSE_PK_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=CourseQualityCheckSerializer(many=True)),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, course_pk):
        course = self._get_course(course_pk)
        quality_check_template_service.refresh_course_quality_checks(course=course)
        results = (
            course.quality_checks.select_related("criterion")
            .filter(criterion__is_active=True)
            .order_by("criterion__section", "criterion__order_index")
        )
        return Response(CourseQualityCheckSerializer(results, many=True).data)

    def _get_course(self, course_pk) -> Course:
        try:
            return collaborator_service.get_courses_accessible_to(
                self.request.user
            ).get(pk=course_pk)
        except Course.DoesNotExist as exc:
            from rest_framework import exceptions

            raise exceptions.NotFound("Course not found.") from exc
