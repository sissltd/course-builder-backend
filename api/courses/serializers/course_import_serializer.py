from rest_framework import serializers

from api.catalog.models import Category, Topic
from api.courses.constants import COURSE_MEDIA_URL_MAX_LENGTH
from api.courses.enums import DifficultyLevel, LessonContentType
from api.courses.models import CourseImportJob
from api.courses.models.lesson_requirement import LESSON_REQUIREMENT_MAX_LENGTH
from api.courses.serializers.assessment_serializer import QuizQuestionSerializer
from api.courses.services import course_import_service


class ImportedAssessmentSerializer(serializers.Serializer):
    title = serializers.CharField(
        max_length=255,
        help_text="Assessment title shown to learners.",
    )
    questions = QuizQuestionSerializer(
        many=True,
        allow_empty=False,
        help_text="Questions in the same shape the assessment endpoints accept.",
    )


def _objectives_field(help_text: str) -> serializers.ListField:
    return serializers.ListField(
        child=serializers.CharField(max_length=500),
        required=False,
        default=list,
        help_text=help_text,
    )


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
        required=False,
        allow_blank=True,
        default="",
        help_text=(
            "Imported lesson body that becomes the lesson script and content block. "
            "Required for TEXT lessons."
        ),
    )
    content_type = serializers.ChoiceField(
        choices=LessonContentType.choices,
        required=False,
        default=LessonContentType.TEXT,
        help_text="Lesson format: TEXT, VIDEO, or QUIZ. Defaults to TEXT.",
    )
    duration_minutes = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        help_text="Lesson length in minutes; estimated from the script when omitted.",
    )
    learning_objectives = _objectives_field("Measurable lesson objectives.")
    requirements = serializers.ListField(
        child=serializers.CharField(max_length=LESSON_REQUIREMENT_MAX_LENGTH),
        required=False,
        default=list,
        help_text="What the learner needs before starting this lesson.",
    )
    video_url = serializers.URLField(
        required=False,
        allow_blank=True,
        default="",
        max_length=COURSE_MEDIA_URL_MAX_LENGTH,
        help_text="Optional lesson video link.",
    )
    embedded_link = serializers.URLField(
        required=False,
        allow_blank=True,
        default="",
        max_length=COURSE_MEDIA_URL_MAX_LENGTH,
        help_text="Optional external player embed link (Vimeo, YouTube, etc.).",
    )
    assessment = ImportedAssessmentSerializer(
        required=False,
        help_text="Lesson-level quiz; required for QUIZ lessons.",
    )

    def validate(self, attrs):
        content_type = attrs.get("content_type", LessonContentType.TEXT)
        if content_type == LessonContentType.TEXT and not attrs.get("content", "").strip():
            raise serializers.ValidationError(
                {"content": "Every TEXT lesson must include imported content."}
            )
        if content_type == LessonContentType.QUIZ and not attrs.get("assessment"):
            raise serializers.ValidationError(
                {"assessment": "QUIZ lessons must include an assessment."}
            )
        return attrs


class ImportedModuleSerializer(serializers.Serializer):
    title = serializers.CharField(
        max_length=255,
        help_text="Module title detected from the document or edited by the creator.",
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional module description; omitted values are saved blank.",
    )
    order = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Display order within the course; omitted values are assigned in order.",
    )
    learning_objectives = _objectives_field("Measurable module objectives.")
    lessons = ImportedLessonSerializer(
        many=True,
        allow_empty=False,
        help_text="Lessons detected beneath this module.",
    )
    assessment = ImportedAssessmentSerializer(
        required=False,
        help_text="Module-level assessment the quality check requires.",
    )

    def validate_lessons(self, lessons):
        _require_unique_orders(lessons, "Lesson order must be unique within each module.")
        return lessons


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
    difficulty_level = serializers.ChoiceField(
        choices=DifficultyLevel.choices,
        required=False,
        allow_blank=True,
        help_text="BEGINNER, INTERMEDIATE, or ADVANCED.",
    )
    tags = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        help_text="Search tags for the course.",
    )
    learning_objectives = serializers.ListField(
        child=serializers.CharField(max_length=500),
        required=False,
        help_text="Measurable course-level objectives.",
    )
    preview_video_url = serializers.URLField(
        required=False,
        allow_blank=True,
        max_length=COURSE_MEDIA_URL_MAX_LENGTH,
        help_text="Optional public preview video link.",
    )
    final_assessment = ImportedAssessmentSerializer(
        required=False,
        help_text="Course-level final assessment.",
    )


class ImportedStructureSerializer(serializers.Serializer):
    course = ImportedCourseMetadataSerializer(
        required=False,
        help_text="Optional reviewed course-level metadata.",
    )
    modules = ImportedModuleSerializer(
        many=True,
        allow_empty=False,
        help_text="Reviewed module and lesson tree to create in the builder.",
    )

    def validate_modules(self, modules):
        _require_unique_orders(modules, "Module order must be unique.")
        return modules


def _require_unique_orders(items: list[dict], message: str) -> None:
    orders = [item["order"] for item in items if item.get("order") is not None]
    if len(orders) != len(set(orders)):
        raise serializers.ValidationError(message)


class CourseImportCreateSerializer(serializers.Serializer):
    file_key = serializers.CharField(
        max_length=500,
        help_text="Durable file_key returned by /api/v1/uploads/presign/.",
    )
    filename = serializers.CharField(
        max_length=255,
        help_text=(
            "Original document filename ending in .pdf, .docx, .txt, .csv, "
            ".xlsx, or .json."
        ),
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
    quality_failures = serializers.ListField(
        child=serializers.CharField(),
        help_text=(
            "Submission quality checks the new draft still fails, e.g. a missing "
            "preview video. Empty when the draft is ready to submit."
        ),
    )


class CourseImportTemplateQuerySerializer(serializers.Serializer):
    file_type = serializers.ChoiceField(
        choices=["xlsx", "json"],
        default="xlsx",
        help_text="Template file type: xlsx (spreadsheet) or json.",
    )
