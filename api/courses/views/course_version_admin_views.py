from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import exceptions, serializers, status
from rest_framework.views import APIView

from api.authorization import codenames
from api.authorization.permissions import Perm
from api.courses.models import CourseVersion
from api.courses.services import course_version_service
from includes.spectacular.responses import (
    STANDARD_ERROR_RESPONSES,
    inline_success_response,
)
from shared.response.success import custom_success_response

TAG = "Admin — Course Versions"
_AUTH = (
    "**Auth:** The `courses.force_version_migration` permission (Force Course "
    "Version Migration) — Admin, Approver and Super Admin by default.\n\n"
)


class AdminCourseVersionSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="Version id.")
    label = serializers.CharField(help_text="Version label, e.g. '2.0'.")
    is_active = serializers.BooleanField(
        help_text="Whether new courses may use it and courses may be moved to it."
    )
    migratable_count = serializers.IntegerField(
        help_text="Unpublished courses on this version that a migration would move."
    )
    draft_count = serializers.IntegerField(help_text="Courses in Draft.")
    needs_revision_count = serializers.IntegerField(
        help_text="Courses in Needs Revision."
    )
    submitted_count = serializers.IntegerField(help_text="Courses Submitted.")
    in_review_count = serializers.IntegerField(help_text="Courses In Review.")
    qa_verification_count = serializers.IntegerField(
        help_text="Courses in QA Verification."
    )
    approved_count = serializers.IntegerField(
        help_text="Courses Approved, not yet published."
    )
    published_count = serializers.IntegerField(
        help_text="Published courses on this version; migrations never move these."
    )


class CourseVersionCreateSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=10, help_text="Version label, e.g. '2.0'.")
    is_active = serializers.BooleanField(
        required=False,
        default=True,
        help_text="Whether new courses may select this version.",
    )

    def validate_label(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("This field may not be blank.")
        return value


class CourseVersionUpdateSerializer(serializers.Serializer):
    label = serializers.CharField(
        max_length=10, required=False, help_text="Replacement version label."
    )
    is_active = serializers.BooleanField(
        required=False,
        help_text="Whether new courses may select this version.",
    )

    def validate_label(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("This field may not be blank.")
        return value


class CourseVersionAdminRepresentationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseVersion
        fields = ["id", "label", "is_active"]
        read_only_fields = fields


class VersionMigrationRequestSerializer(serializers.Serializer):
    from_version_id = serializers.UUIDField(help_text="Version to move courses off.")
    to_version_id = serializers.UUIDField(help_text="Active version to move them to.")
    dry_run = serializers.BooleanField(
        default=False, help_text="Report what would move without changing anything."
    )


class VersionReferenceSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="Version id.")
    label = serializers.CharField(help_text="Version label.")


class VersionMigrationResultSerializer(serializers.Serializer):
    from_version = VersionReferenceSerializer(help_text="Version courses moved off.")
    to_version = VersionReferenceSerializer(help_text="Version courses moved to.")
    dry_run = serializers.BooleanField(help_text="True if nothing was changed.")
    courses_moved = serializers.IntegerField(
        help_text="Courses moved (or that would move)."
    )
    courses_by_status = serializers.DictField(
        child=serializers.IntegerField(),
        help_text="The moved courses, counted by status.",
    )


class AdminCourseVersionListView(APIView):
    permission_classes = [Perm(codenames.COURSES_FORCE_VERSION_MIGRATION)]
    serializer_class = AdminCourseVersionSerializer  # schema generation only

    @extend_schema(
        summary="List course versions",
        description=(
            "Returns every course version, active or retired, with how many "
            "courses sit on it by status.\n\n"
            "Called when opening the version migration screen, to pick a source "
            "and target.\n\n" + _AUTH + "**Prerequisites:** None.\n\n"
            "**Important:** Not paginated; versions are few. `migratable_count` "
            "is what a migration off that version would move."
        ),
        tags=[TAG],
        responses={
            200: inline_success_response(
                description="Versions with course counts.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "success": True,
                            "status": 200,
                            "message": "Retrieved successfully",
                            "data": [
                                {
                                    "id": "5b0c7f1e-8a61-4c2e-9d4b-3f2a1e6c7d90",
                                    "label": "1.0",
                                    "is_active": False,
                                    "migratable_count": 4,
                                    "draft_count": 3,
                                    "needs_revision_count": 0,
                                    "submitted_count": 1,
                                    "in_review_count": 0,
                                    "qa_verification_count": 0,
                                    "approved_count": 0,
                                    "published_count": 12,
                                }
                            ],
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Retrieved successfully",
            data=AdminCourseVersionSerializer(
                course_version_service.list_versions(actor=request.user), many=True
            ).data,
        )

    @extend_schema(
        summary="Create a course version",
        description=(
            "Creates a canonical version label that creators can select for "
            "future draft courses.\n\n"
            + _AUTH
            + "**Prerequisites:** The label must be unique and non-blank."
        ),
        tags=[TAG],
        request=CourseVersionCreateSerializer,
        examples=[
            OpenApiExample(
                name="Create active version",
                request_only=True,
                value={"label": "3.0", "is_active": True},
            )
        ],
        responses={
            201: inline_success_response(
                description="Course version created.",
                examples=[
                    OpenApiExample(
                        name="Created",
                        value={
                            "success": True,
                            "status": 201,
                            "message": "Course version created.",
                            "data": {
                                "id": "5b0c7f1e-8a61-4c2e-9d4b-3f2a1e6c7d90",
                                "label": "3.0",
                                "is_active": True,
                            },
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = CourseVersionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        version = course_version_service.create_course_version(
            actor=request.user, request=request, **serializer.validated_data
        )
        return custom_success_response(
            status=status.HTTP_201_CREATED,
            message="Course version created.",
            data=CourseVersionAdminRepresentationSerializer(version).data,
        )


class AdminCourseVersionDetailView(APIView):
    permission_classes = [Perm(codenames.COURSES_FORCE_VERSION_MIGRATION)]
    serializer_class = CourseVersionUpdateSerializer

    def _get_version(self, pk):
        try:
            return CourseVersion.objects.get(pk=pk)
        except CourseVersion.DoesNotExist as exc:
            raise exceptions.NotFound("Course version not found.") from exc

    @extend_schema(
        summary="Update a course version",
        description=(
            "Renames a canonical version or activates/retire it. Published "
            "versions cannot be renamed, and at least one active version must "
            "remain available.\n\n" + _AUTH
        ),
        tags=[TAG],
        request=CourseVersionUpdateSerializer,
        examples=[
            OpenApiExample(
                name="Rename and activate version",
                request_only=True,
                value={"label": "3.0.1", "is_active": True},
            )
        ],
        responses={
            200: inline_success_response(
                description="Course version updated.",
                examples=[
                    OpenApiExample(
                        name="Updated",
                        value={
                            "success": True,
                            "status": 200,
                            "message": "Course version updated.",
                            "data": {
                                "id": "5b0c7f1e-8a61-4c2e-9d4b-3f2a1e6c7d90",
                                "label": "3.0.1",
                                "is_active": True,
                            },
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def patch(self, request, pk):
        version = self._get_version(pk)
        serializer = self.serializer_class(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        version = course_version_service.update_course_version(
            actor=request.user,
            version=version,
            request=request,
            **serializer.validated_data,
        )
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Course version updated.",
            data=CourseVersionAdminRepresentationSerializer(version).data,
        )


class CourseVersionMigrationView(APIView):
    permission_classes = [Perm(codenames.COURSES_FORCE_VERSION_MIGRATION)]
    serializer_class = VersionMigrationResultSerializer  # schema generation only

    @extend_schema(
        summary="Move courses to another version",
        description=(
            "Moves every unpublished course (Draft through Approved) on one "
            "version to another, so they publish under it.\n\n"
            + _AUTH
            + "**Prerequisites:** Both versions must exist and differ; the target "
            "must be active.\n\n"
            "**Important:** Published courses are never moved - their snapshot "
            "keeps the version they went out under. Run with `dry_run: true` "
            "first to show the admin what would change. Each moved course is "
            "recorded in the activity log. Creators are not notified."
        ),
        tags=[TAG],
        request=VersionMigrationRequestSerializer,
        examples=[
            OpenApiExample(
                name="Dry run",
                request_only=True,
                value={
                    "from_version_id": "5b0c7f1e-8a61-4c2e-9d4b-3f2a1e6c7d90",
                    "to_version_id": "a1d9e3c4-2b7f-4e10-8c5a-6f9b0d2e1c33",
                    "dry_run": True,
                },
            )
        ],
        responses={
            200: inline_success_response(
                description="What moved, or would move.",
                examples=[
                    OpenApiExample(
                        name="Moved",
                        value={
                            "success": True,
                            "status": 200,
                            "message": "Courses moved.",
                            "data": {
                                "from_version": {
                                    "id": "5b0c7f1e-8a61-4c2e-9d4b-3f2a1e6c7d90",
                                    "label": "1.0",
                                },
                                "to_version": {
                                    "id": "a1d9e3c4-2b7f-4e10-8c5a-6f9b0d2e1c33",
                                    "label": "2.0",
                                },
                                "dry_run": False,
                                "courses_moved": 4,
                                "courses_by_status": {"DRAFT": 3, "SUBMITTED": 1},
                            },
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = VersionMigrationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = course_version_service.migrate_course_versions(
            actor=request.user, request=request, **serializer.validated_data
        )
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Dry run complete." if result["dry_run"] else "Courses moved.",
            data=VersionMigrationResultSerializer(result).data,
        )
