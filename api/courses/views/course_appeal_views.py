from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from api.courses.models import CourseAppeal
from api.courses.serializers import (
    CourseAppealCreateSerializer,
    CourseAppealDecisionSerializer,
    CourseAppealSerializer,
)
from api.courses.services import course_appeal_service
from api.users.permissions import IsAdminOrSuperAdminRole, IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

MANAGE_ACTIONS = {"approve", "reject"}

_COURSE_MINI_EXAMPLE = {
    "id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
    "title": "Django REST Framework Bootcamp",
    "status": "DRAFT",
}

_APPEAL_EXAMPLE = {
    "id": "f6a7b8c9-d0e1-4f2a-3b4c-5d6e7f8a9b0c",
    "course": _COURSE_MINI_EXAMPLE,
    "title": "Rejection was based on outdated content",
    "email": "creator@example.com",
    "web_link": "https://example.com/portfolio",
    "description": "The reviewer flagged Module 2 as outdated, but it was updated last week.",
    "status": "PENDING",
    "decision_notes": "",
    "reviewed_at": None,
    "created_datetime": "2026-08-06T11:00:00.000Z",
}


@extend_schema_view(
    list=extend_schema(
        summary="List course-rejection appeals",
        description=(
            "Returns the caller's own appeals, or every appeal on the "
            "platform for an Admin/Super Admin (the Support review queue).\n\n"
            "**Auth:** Course Creator/Writer (own appeals), or Admin/Super "
            "Admin (every appeal).\n\n"
            "**Prerequisites:** None beyond holding one of those roles. "
            "Results are paginated."
        ),
        tags=["Creator — Appeals"],
        responses={
            200: OpenApiResponse(
                response=CourseAppealSerializer(many=True),
                description="Course-rejection appeals.",
                examples=[OpenApiExample(name="Success", value=[_APPEAL_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a course-rejection appeal",
        description=(
            "Returns a single appeal.\n\n"
            "**Auth:** The submitting creator, or Admin/Super Admin.\n\n"
            "**Prerequisites:** The appeal must exist and be visible to the "
            "caller.\n\n"
            "**Important:** A creator requesting someone else's appeal gets "
            "404, not 403 - existence isn't leaked."
        ),
        tags=["Creator — Appeals"],
        responses={
            200: OpenApiResponse(
                response=CourseAppealSerializer,
                description="The requested appeal.",
                examples=[OpenApiExample(name="Success", value=_APPEAL_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="File an appeal against a course rejection",
        description=(
            "Submits a Pending appeal disputing a course's rejection (PRD "
            'Section 12: "Creator disputes rejection... Creator submits '
            'written dispute through platform..."), and notifies Admins/'
            "Super Admins in-app. This is the Figma 'Request for an appeal' "
            "form.\n\n"
            "Called from the Support page's 'Request for an appeal' "
            "action.\n\n"
            "**Auth:** Course Creator or Writer.\n\n"
            "**Prerequisites:** `course` must belong to the caller and must "
            "currently be in the just-rejected state (returned to Draft "
            "with `rejected_at` set - see the review-queue reject action). "
            "No other appeal for the same course may still be Pending.\n\n"
            "**Important:** `email` is captured as typed on the form, not "
            "forced to the account's login email."
        ),
        tags=["Creator — Appeals"],
        request=CourseAppealCreateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "course": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
                    "title": "Rejection was based on outdated content",
                    "email": "creator@example.com",
                    "web_link": "https://example.com/portfolio",
                    "description": "The reviewer flagged Module 2 as outdated, but it was updated last week.",
                },
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=CourseAppealSerializer,
                description="Appeal created as Pending.",
                examples=[OpenApiExample(name="Success", value=_APPEAL_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    update=extend_schema(exclude=True),
    partial_update=extend_schema(exclude=True),
    destroy=extend_schema(exclude=True),
)
class CourseAppealViewSet(ModelViewSet):
    """A creator's appeals against a course rejection (PRD Section 12), plus
    Admin/Super Admin approve/reject. Approving reopens the course for
    review (status -> SUBMITTED); rejecting is final and just closes the
    appeal out with `decision_notes`."""

    http_method_names = ["get", "post", "head", "options"]
    permission_classes = [IsCourseCreatorRole | IsAdminOrSuperAdminRole]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CourseAppeal.objects.none()

        queryset = CourseAppeal.objects.select_related(
            "course", "submitted_by", "reviewed_by"
        )
        if self.request.user.is_superuser or self.request.user.role in (
            IsAdminOrSuperAdminRole.allowed_roles
        ):
            return queryset
        return queryset.filter(submitted_by=self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return CourseAppealCreateSerializer
        return CourseAppealSerializer

    def get_permissions(self):
        if self.action in MANAGE_ACTIONS:
            return [IsAdminOrSuperAdminRole()]
        return super().get_permissions()

    @extend_schema(
        summary="Approve a course-rejection appeal",
        description=(
            "Approves a Pending appeal: reopens the course for review "
            "(status -> SUBMITTED) and notifies the creator.\n\n"
            "Called from the 'Approve' action on the Support review "
            "queue.\n\n"
            "**Auth:** Admin or Super Admin.\n\n"
            "**Prerequisites:** The appeal must be `PENDING`.\n\n"
            "**Important:** Per PRD wording, the decision is final - an "
            "already-decided appeal cannot be re-decided."
        ),
        tags=["Admin — Appeals"],
        request=CourseAppealDecisionSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={"notes": "Module 2 was confirmed up to date."},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=CourseAppealSerializer,
                description="Appeal approved, course resubmitted for review.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            **_APPEAL_EXAMPLE,
                            "status": "APPROVED",
                            "course": {**_COURSE_MINI_EXAMPLE, "status": "SUBMITTED"},
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                description="The appeal isn't Pending.",
                examples=[
                    OpenApiExample(
                        name="Wrong status",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "Appeal cannot be approved from status 'APPROVED'.",
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        serializer = CourseAppealDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        appeal = course_appeal_service.approve_appeal(
            appeal=self.get_object(),
            actor=request.user,
            notes=serializer.validated_data["notes"],
        )
        return Response(CourseAppealSerializer(appeal).data)

    @extend_schema(
        summary="Reject a course-rejection appeal",
        description=(
            "Rejects a Pending appeal with optional free-text decision "
            "notes. The course is left untouched (still Draft). This "
            "decision is final, per the PRD.\n\n"
            "Called from the 'Reject' action on the Support review "
            "queue.\n\n"
            "**Auth:** Admin or Super Admin.\n\n"
            "**Prerequisites:** The appeal must be `PENDING`."
        ),
        tags=["Admin — Appeals"],
        request=CourseAppealDecisionSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={"notes": "Original rejection stands after review."},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=CourseAppealSerializer,
                description="Appeal rejected.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            **_APPEAL_EXAMPLE,
                            "status": "REJECTED",
                            "decision_notes": "Original rejection stands after review.",
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                description="The appeal isn't Pending.",
                examples=[
                    OpenApiExample(
                        name="Wrong status",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "Appeal cannot be rejected from status 'REJECTED'.",
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        serializer = CourseAppealDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        appeal = course_appeal_service.reject_appeal(
            appeal=self.get_object(),
            actor=request.user,
            notes=serializer.validated_data["notes"],
        )
        return Response(CourseAppealSerializer(appeal).data)
