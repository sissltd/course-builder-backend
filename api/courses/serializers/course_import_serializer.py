from rest_framework import serializers

from api.catalog.models import Category, Topic
from api.courses.models import CourseImportJob
from api.courses.services import course_import_service


class ImportedLessonSerializer(serializers.Serializer):
    title = serializers.CharField(
        max_length=255,
        help_text="Lesson title detected from the document or edited by the creator.",
    )
    order = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Display order within the module; omitted values are assigned in order.",
    )
    content = serializers.CharField(
        help_text="Imported lesson body that becomes the lesson script and content block."
    )


class ImportedModuleSerializer(serializers.Serializer):
    title = serializers.CharField(
        max_length=255,
        help_text="Module title detected from the document or edited by the creator.",
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Optional module description; omitted values are saved blank.",
    )
    order = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Display order within the course; omitted values are assigned in order.",
    )
    lessons = ImportedLessonSerializer(
        many=True, help_text="Lessons detected beneath this module."
    )


class ImportedCourseMetadataSerializer(serializers.Serializer):
    title = serializers.CharField(
        required=False,
        max_length=255,
        help_text="Optional reviewed course title; defaults to the title from import start.",
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Optional reviewed course description; defaults to the import description.",
    )


class ImportedStructureSerializer(serializers.Serializer):
    course = ImportedCourseMetadataSerializer(
        required=False,
        help_text="Optional reviewed course-level metadata.",
    )
    modules = ImportedModuleSerializer(
        many=True,
        help_text="Reviewed module and lesson tree to create in the builder.",
    )


class CourseImportCreateSerializer(serializers.Serializer):
    file_key = serializers.CharField(
        max_length=500,
        help_text="Durable file_key returned by /api/v1/uploads/presign/.",
    )
    filename = serializers.CharField(
        max_length=255,
        help_text="Original document filename ending in .pdf, .docx, .txt, or .csv.",
    )
    content_type = serializers.CharField(
        max_length=120,
        help_text="Uploaded document MIME type.",
    )
    size = serializers.IntegerField(
        min_value=1,
        help_text="Uploaded document size in bytes; must be 20MB or less.",
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        help_text="UUID of the category for the imported course.",
    )
    topic = serializers.PrimaryKeyRelatedField(
        queryset=Topic.objects.all(),
        required=False,
        allow_null=True,
        help_text="Optional topic UUID; must belong to the selected category.",
    )
    title = serializers.CharField(
        max_length=255,
        help_text="Course title to use when the import is confirmed.",
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Optional course description to use when the import is confirmed.",
    )
    terms_accepted = serializers.BooleanField(
        help_text="Whether the creator accepted the selected category terms."
    )
    idempotency_key = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Client-generated key reused when retrying the same import start.",
    )

    def validate(self, attrs):
        if not attrs["terms_accepted"]:
            raise serializers.ValidationError(
                {"terms_accepted": "You must accept the category Terms and Conditions."}
            )
        course_import_service.validate_import_file_metadata(
            file_key=attrs["file_key"],
            filename=attrs["filename"],
            content_type=attrs["content_type"],
            size=attrs["size"],
        )
        topic = attrs.get("topic")
        if topic and topic.category_id != attrs["category"].id:
            raise serializers.ValidationError(
                {"topic": "Topic does not belong to the selected category."}
            )
        return attrs


class CourseImportJobSerializer(serializers.ModelSerializer):
    course_id = serializers.UUIDField(source="course.id", read_only=True)

    class Meta:
        model = CourseImportJob
        fields = [
            "id",
            "course_id",
            "status",
            "progress",
            "stage",
            "file_key",
            "filename",
            "content_type",
            "size",
            "title",
            "description",
            "detected_structure",
            "warnings",
            "error_message",
            "cancel_requested",
            "created_datetime",
            "updated_datetime",
            "completed_at",
        ]
        read_only_fields = fields


class CourseImportConfirmSerializer(serializers.Serializer):
    structure = ImportedStructureSerializer(
        required=False,
        help_text=(
            "Reviewed import tree. Omit to confirm the latest detected_structure "
            "unchanged."
        ),
    )


class CourseImportConfirmResponseSerializer(serializers.Serializer):
    course_id = serializers.UUIDField(help_text="Created draft course UUID.")
    status = serializers.CharField(help_text="Created course lifecycle status.")
    builder_url = serializers.CharField(
        help_text="Frontend-relative builder URL for the created course."
    )
