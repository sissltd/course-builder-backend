import csv
import io
import re

from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.catalog.enums import CategoryStatus
from api.courses.enums import CourseImportStatus, CourseSourceType
from api.courses.models import CourseImportJob, Lesson, LessonContentBlock, Module
from api.courses.services import course_service
from api.authorization import codenames
from api.authorization.services import permission_service
from shared.services.storage_service import StorageService

DOCUMENT_IMPORT_PURPOSE = "COURSE_DOCUMENT_IMPORT"
DOCUMENT_IMPORT_FOLDER = "course-imports"
DOCUMENT_IMPORT_MAX_SIZE = 20 * 1024 * 1024

DOCUMENT_IMPORT_TYPES = {
    "application/pdf": {"pdf"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {"docx"},
    "text/plain": {"txt"},
    "text/csv": {"csv"},
    "application/csv": {"csv"},
}
DOCUMENT_IMPORT_EXTENSIONS = {"pdf", "docx", "txt", "csv"}

IN_FLIGHT_STATUSES = (
    CourseImportStatus.QUEUED,
    CourseImportStatus.PARSING,
    CourseImportStatus.MAPPING,
    CourseImportStatus.READY_FOR_REVIEW,
    CourseImportStatus.CONFIRMING,
)
TERMINAL_STATUSES = (
    CourseImportStatus.COMPLETED,
    CourseImportStatus.FAILED,
    CourseImportStatus.CANCELLED,
)

FALLBACK_WARNING = (
    "No clear section structure was detected, so the document was imported "
    "as one module and one lesson."
)


def extension_for(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def validate_import_file_metadata(
    *, file_key: str, filename: str, content_type: str, size: int
) -> None:
    if not file_key:
        raise exceptions.ValidationError({"file_key": "Uploaded document file_key is required."})
    if not file_key.startswith(f"uploads/{DOCUMENT_IMPORT_FOLDER}/"):
        raise exceptions.ValidationError(
            {"file_key": "file_key must reference a course-imports upload."}
        )
    if size <= 0:
        raise exceptions.ValidationError(
            {"size": "The selected document is empty. Upload a document with content."}
        )
    if size > DOCUMENT_IMPORT_MAX_SIZE:
        raise exceptions.ValidationError(
            {"size": "Document uploads cannot exceed 20MB."}
        )
    extension = extension_for(filename)
    if extension not in DOCUMENT_IMPORT_EXTENSIONS:
        raise exceptions.ValidationError(
            {
                "filename": (
                    "Filename must end with .pdf, .docx, .txt, or .csv."
                )
            }
        )
    allowed_extensions = DOCUMENT_IMPORT_TYPES.get(content_type)
    if allowed_extensions is None:
        raise exceptions.ValidationError(
            {
                "content_type": (
                    "Unsupported document format. Upload a PDF, DOCX, TXT, or CSV file."
                )
            }
        )
    if extension not in allowed_extensions:
        raise exceptions.ValidationError(
            {
                "filename": (
                    "Filename extension does not match the uploaded document type."
                )
            }
        )


@transaction.atomic
def create_import_job(*, creator, validated_data):
    permission_service.require_permission(creator, codenames.COURSES_CREATE)
    key = validated_data.get("idempotency_key", "")
    if key:
        existing = CourseImportJob.objects.filter(
            creator=creator, idempotency_key=key
        ).first()
        if existing:
            return existing, False
    if CourseImportJob.objects.filter(
        creator=creator, status__in=IN_FLIGHT_STATUSES
    ).exists():
        raise exceptions.Throttled(
            "You already have a document import in progress. Poll that job or "
            "cancel it before starting another."
        )
    category = validated_data["category"]
    topic = validated_data.get("topic")
    if category.status != CategoryStatus.ACTIVE:
        raise exceptions.ValidationError(
            {"category": "Category not found or not accepting submissions."}
        )
    if topic and topic.category_id != category.id:
        raise exceptions.ValidationError(
            {"topic": "Topic does not belong to the selected category."}
        )
    job = CourseImportJob.objects.create(
        creator=creator,
        category=category,
        topic=topic,
        file_key=validated_data["file_key"],
        filename=validated_data["filename"],
        content_type=validated_data["content_type"],
        size=validated_data["size"],
        title=validated_data["title"],
        description=validated_data.get("description", ""),
        idempotency_key=key,
        stage="Queued for parsing",
    )
    return job, True


def process_import_job(*, job: CourseImportJob) -> CourseImportJob:
    if job.status in TERMINAL_STATUSES or job.cancel_requested:
        return job
    try:
        job.status = CourseImportStatus.PARSING
        job.stage = "Parsing document"
        job.progress = 30
        job.started_at = job.started_at or timezone.now()
        job.save(
            update_fields=["status", "stage", "progress", "started_at", "updated_datetime"]
        )
        structure, warnings = parse_document(job)
        if job.cancel_requested:
            job.status = CourseImportStatus.CANCELLED
            job.stage = "Import cancelled"
            job.completed_at = timezone.now()
            job.save(
                update_fields=["status", "stage", "completed_at", "updated_datetime"]
            )
            return job
        job.status = CourseImportStatus.MAPPING
        job.stage = "Mapping document to course structure"
        job.progress = 75
        job.save(update_fields=["status", "stage", "progress", "updated_datetime"])
        job.detected_structure = structure
        job.warnings = warnings
        job.status = CourseImportStatus.READY_FOR_REVIEW
        job.stage = "Ready for review"
        job.progress = 100
        job.save(
            update_fields=[
                "detected_structure",
                "warnings",
                "status",
                "stage",
                "progress",
                "updated_datetime",
            ]
        )
    except Exception as exc:
        job.status = CourseImportStatus.FAILED
        job.stage = "Import failed"
        job.progress = 100
        job.error_message = str(exc)
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "stage",
                "progress",
                "error_message",
                "completed_at",
                "updated_datetime",
            ]
        )
    return job


def cancel_import_job(*, job: CourseImportJob, actor) -> CourseImportJob:
    if job.creator_id != actor.id:
        raise exceptions.NotFound("Import job not found.")
    if job.status in TERMINAL_STATUSES:
        return job
    job.cancel_requested = True
    job.status = CourseImportStatus.CANCELLED
    job.stage = "Import cancelled"
    job.completed_at = timezone.now()
    job.save(
        update_fields=[
            "cancel_requested",
            "status",
            "stage",
            "completed_at",
            "updated_datetime",
        ]
    )
    return job


def parse_document(job: CourseImportJob) -> tuple[dict, list[str]]:
    extension = extension_for(job.filename)
    if extension == "csv":
        raw = StorageService.download_bytes(job.file_key)
        return parse_csv(raw)
    if extension == "txt":
        raw = StorageService.download_bytes(job.file_key)
        return parse_text(raw.decode("utf-8-sig"))
    if extension == "pdf":
        return stub_document_structure("Imported PDF Document")
    if extension == "docx":
        return stub_document_structure("Imported DOCX Document")
    raise exceptions.ValidationError(
        "Unsupported document format. Upload a PDF, DOCX, TXT, or CSV file."
    )


def parse_csv(raw: bytes) -> tuple[dict, list[str]]:
    try:
        text = raw.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
    except Exception as exc:
        raise exceptions.ValidationError(
            "This CSV could not be parsed. Check the file formatting and retry."
        ) from exc
    if not text.strip():
        raise exceptions.ValidationError("This CSV has no content rows to import.")
    if not rows:
        raise exceptions.ValidationError(
            "This CSV only contains headers. Add content rows and retry."
        )
    headers = [header or "" for header in (rows[0].keys() if rows else [])]
    title_key = _find_header(headers, {"lesson", "lesson_title", "title", "topic"})
    module_key = _find_header(headers, {"module", "module_title", "section", "chapter"})
    content_key = _find_header(headers, {"content", "body", "text", "script", "description"})
    if content_key is None and len(headers) >= 2:
        content_key = headers[-1]
    modules: list[dict] = []
    module_indexes: dict[str, int] = {}
    for row_number, row in enumerate(rows, 1):
        content = (row.get(content_key) or "").strip() if content_key else ""
        lesson_title = (row.get(title_key) or "").strip() if title_key else ""
        module_title = (row.get(module_key) or "").strip() if module_key else ""
        if not content and not lesson_title:
            continue
        if not module_title:
            module_title = "Imported CSV Content"
        if module_title not in module_indexes:
            module_indexes[module_title] = len(modules)
            modules.append(
                {
                    "title": module_title,
                    "description": "",
                    "order": len(modules) + 1,
                    "lessons": [],
                }
            )
        lessons = modules[module_indexes[module_title]]["lessons"]
        lessons.append(
            {
                "title": lesson_title or f"Imported Row {row_number}",
                "order": len(lessons) + 1,
                "content": content or lesson_title,
            }
        )
    if not modules:
        raise exceptions.ValidationError("This CSV has no content rows to import.")
    return {"modules": modules}, []


def parse_text(text: str) -> tuple[dict, list[str]]:
    if not text.strip():
        raise exceptions.ValidationError("No readable content was found in this document.")
    headings = []
    current_heading = None
    current_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if _looks_like_heading(line):
            if current_heading or current_lines:
                headings.append(
                    (
                        current_heading or "Imported Lesson",
                        "\n".join(current_lines).strip(),
                    )
                )
            current_heading = _clean_heading(line)
            current_lines = []
        elif line:
            current_lines.append(line)
    if current_heading or current_lines:
        headings.append((current_heading or "Imported Lesson", "\n".join(current_lines).strip()))
    if len(headings) <= 1:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        content = "\n\n".join(paragraphs)
        return fallback_structure(content), [FALLBACK_WARNING]
    lessons = [
        {"title": title[:255], "order": index, "content": content or title}
        for index, (title, content) in enumerate(headings, 1)
    ]
    return {
        "modules": [
            {
                "title": "Imported Document",
                "description": "",
                "order": 1,
                "lessons": lessons,
            }
        ]
    }, []


def stub_document_structure(title: str) -> tuple[dict, list[str]]:
    return fallback_structure(
        "Document parsing for this file type is queued for backend parser integration."
    ), [
        f"{title} was accepted, but detailed parser extraction is not enabled yet.",
        FALLBACK_WARNING,
    ]


def fallback_structure(content: str) -> dict:
    return {
        "modules": [
            {
                "title": "Imported Document",
                "description": "",
                "order": 1,
                "lessons": [
                    {
                        "title": "Imported Lesson",
                        "order": 1,
                        "content": content,
                    }
                ],
            }
        ]
    }


def _looks_like_heading(line: str) -> bool:
    if not line:
        return False
    return bool(
        line.startswith("#")
        or re.match(r"^(chapter|section|module|lesson)\s+\d+", line, re.I)
        or re.match(r"^\d+(\.\d+)*[\).]?\s+\S+", line)
    )


def _clean_heading(line: str) -> str:
    line = line.lstrip("#").strip()
    return re.sub(r"^\d+(\.\d+)*[\).]?\s+", "", line).strip() or "Imported Lesson"


def _find_header(headers, candidates: set[str]) -> str | None:
    normalized = {header.lower().strip().replace(" ", "_"): header for header in headers}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


@transaction.atomic
def confirm_import_job(*, job: CourseImportJob, actor, structure: dict | None = None):
    if job.creator_id != actor.id:
        raise exceptions.NotFound("Import job not found.")
    if job.status == CourseImportStatus.COMPLETED:
        raise exceptions.ValidationError("This import has already been confirmed.")
    if job.status == CourseImportStatus.CANCELLED:
        raise exceptions.ValidationError("Cancelled imports cannot be confirmed.")
    if job.status == CourseImportStatus.FAILED:
        raise exceptions.ValidationError(
            "Failed imports cannot be confirmed. Retry with a new upload."
        )
    if job.status != CourseImportStatus.READY_FOR_REVIEW:
        raise exceptions.ValidationError(
            "Import cannot be confirmed until parsing is ready for review."
        )
    reviewed = structure or job.detected_structure
    _validate_structure(reviewed)
    job.status = CourseImportStatus.CONFIRMING
    job.stage = "Creating course"
    job.save(update_fields=["status", "stage", "updated_datetime"])
    try:
        course = course_service.create_draft_course(
            creator=job.creator,
            category=job.category,
            topic=job.topic,
            title=reviewed.get("course", {}).get("title") or job.title,
            description=(
                reviewed.get("course", {}).get("description")
                or job.description
                or job.title
            ),
            terms_accepted=True,
            source_type=CourseSourceType.DOCUMENT_IMPORTED,
        )
        for module_order, module_data in enumerate(reviewed["modules"], 1):
            module = Module.objects.create(
                course=course,
                title=module_data["title"],
                description=module_data.get("description", ""),
                order=module_data.get("order") or module_order,
                learning_objectives=[],
                created_by=job.creator,
                updated_by=job.creator,
            )
            for lesson_order, lesson_data in enumerate(module_data["lessons"], 1):
                content = lesson_data["content"]
                lesson = Lesson.objects.create(
                    module=module,
                    title=lesson_data["title"],
                    order=lesson_data.get("order") or lesson_order,
                    script=content,
                    learning_objectives=[],
                    duration_minutes=max(1, len(content.split()) // 130),
                    created_by=job.creator,
                    updated_by=job.creator,
                )
                LessonContentBlock.objects.create(
                    lesson=lesson,
                    order=1,
                    block_type=LessonContentBlock.BlockType.PARAGRAPH,
                    text_content=content,
                    created_by=job.creator,
                    updated_by=job.creator,
                )
        course_service.recalculate_duration_estimate(course=course)
        job.course = course
        job.status = CourseImportStatus.COMPLETED
        job.stage = "Course created"
        job.progress = 100
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "course",
                "status",
                "stage",
                "progress",
                "completed_at",
                "updated_datetime",
            ]
        )
        return course
    except Exception as exc:
        job.status = CourseImportStatus.FAILED
        job.stage = "Confirmation failed"
        job.error_message = str(exc)
        job.completed_at = timezone.now()
        job.save(
            update_fields=["status", "stage", "error_message", "completed_at", "updated_datetime"]
        )
        raise


def _validate_structure(structure: dict) -> None:
    modules = structure.get("modules") or []
    if not modules:
        raise exceptions.ValidationError("At least one module is required.")
    module_orders = []
    for module in modules:
        if not (module.get("title") or "").strip():
            raise exceptions.ValidationError("Every module must have a title.")
        lessons = module.get("lessons") or []
        if not lessons:
            raise exceptions.ValidationError("Every module must contain at least one lesson.")
        module_orders.append(module.get("order"))
        lesson_orders = []
        for lesson in lessons:
            if not (lesson.get("title") or "").strip():
                raise exceptions.ValidationError("Every lesson must have a title.")
            if not (lesson.get("content") or "").strip():
                raise exceptions.ValidationError("Every lesson must include imported content.")
            lesson_orders.append(lesson.get("order"))
        compact_lesson_orders = [order for order in lesson_orders if order is not None]
        if len(compact_lesson_orders) != len(set(compact_lesson_orders)):
            raise exceptions.ValidationError("Lesson order must be unique within each module.")
    compact_module_orders = [order for order in module_orders if order is not None]
    if len(compact_module_orders) != len(set(compact_module_orders)):
        raise exceptions.ValidationError("Module order must be unique.")
