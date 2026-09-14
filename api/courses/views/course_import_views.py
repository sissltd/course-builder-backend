from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.courses.enums import CourseImportStatus
from api.courses.models import CourseImportJob
from api.courses.serializers.course_import_serializer import (
    CourseImportConfirmResponseSerializer,
    CourseImportConfirmSerializer,
    CourseImportCreateSerializer,
    CourseImportJobSerializer,
)
from api.courses.services import course_import_service
from api.users.permissions import IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

COURSE_IMPORT_TAG = ["Creator — Course Imports"]

JOB_EXAMPLE = {
    "id": "5c7f1f8d-1f96-4f7a-9e1a-3c42d37e0d22",
    "course_id": None,
    "status": "READY_FOR_REVIEW",
    "progress": 100,
    "stage": "Ready for review",
    "file_key": "uploads/course-imports/outline.csv",
    "filename": "python-outline.csv",
    "content_type": "text/csv",
    "size": 2048,
    "title": "Python Basics from CSV",
    "description": "Imported from a document.",
    "detected_structure": {
        "modules": [
            {
                "title": "Getting Started",
                "description": "",
                "order": 1,
                "lessons": [
                    {
                        "title": "Installing Python",
                        "order": 1,
                        "content": "Install Python and verify it from the terminal.",
                    }
                ],
            }
        ]
    },
    "warnings": [],
    "error_message": "",
    "cancel_requested": False,
    "created_datetime": "2026-09-14T09:00:00Z",
    "updated_datetime": "2026-09-14T09:00:05Z",
    "completed_at": None,
}


class CourseImportListCreateView(APIView):
    permission_classes = [IsCourseCreatorRole]
    serializer_class = CourseImportCreateSerializer

    @extend_schema(
        summary="Start a document course import",
        description=(
            "Starts a creator-owned document import from a previously uploaded "
            "PDF, DOCX, TXT, or CSV file. The API validates the durable upload "
            "`file_key`, parses the document into a draft module/lesson tree, "
            "and returns an import job for the frontend to poll or review.\n\n"
            "Call this after `/api/v1/uploads/presign/` succeeds and the browser "
            "has uploaded the file to object storage.\n\n"
            "**Auth:** Course Creator or invited Staff Writer.\n\n"
            "**Prerequisites:** The uploaded file must use `folder=course-imports` "
            "and `purpose=COURSE_DOCUMENT_IMPORT`; the selected category must be "
            "active, and any topic must belong to that category.\n\n"
            "**Important:** Send the durable `file_key`, not the temporary "
            "`file_url`. CSV and TXT are parsed immediately in this slice; PDF "
            "and DOCX are accepted under the final contract and return a safe "
            "fallback structure until parser extraction is enabled."
        ),
        request=CourseImportCreateSerializer,
        examples=[
            OpenApiExample(
                name="Start CSV import",
                request_only=True,
                value={
                    "file_key": "uploads/course-imports/python-outline.csv",
                    "filename": "python-outline.csv",
                    "content_type": "text/csv",
                    "size": 2048,
                    "category": "6f9619ff-8b86-d011-b42d-00cf4fc964ff",
                    "topic": None,
                    "title": "Python Basics from CSV",
                    "description": "Imported from a document.",
                    "terms_accepted": True,
                    "idempotency_key": "doc-import-20260914-001",
                },
            )
        ],
        responses={
            200: OpenApiResponse(
                response=CourseImportJobSerializer,
                description="Existing import job for the supplied idempotency key.",
                examples=[OpenApiExample(name="Existing job", value=JOB_EXAMPLE)],
            ),
            202: OpenApiResponse(
                response=CourseImportJobSerializer,
                description="Document import job created.",
                examples=[OpenApiExample(name="Ready for review", value=JOB_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["rate_limited"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
        tags=COURSE_IMPORT_TAG,
    )
    def post(self, request):
        serializer = CourseImportCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job, created = course_import_service.create_import_job(
            creator=request.user, validated_data=serializer.validated_data
        )
        if created:
            job = course_import_service.process_import_job(job=job)
        return Response(
            CourseImportJobSerializer(job).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )


class CourseImportDetailView(APIView):
    permission_classes = [IsCourseCreatorRole]
    serializer_class = CourseImportJobSerializer

    def get_object(self, request, pk):
        return CourseImportJob.objects.select_related("course", "category", "topic").filter(
            pk=pk, creator=request.user
        ).first() or (_ for _ in ()).throw(exceptions.NotFound("Import job not found."))

    @extend_schema(
        summary="Retrieve document import progress",
        description=(
            "Returns the latest status of a creator-owned document import job, "
            "including parser progress, warnings, errors, and the detected "
            "module/lesson tree when ready for review.\n\n"
            "Poll this endpoint every 2–3 seconds after starting an import, and "
            "stop when status becomes `READY_FOR_REVIEW`, `COMPLETED`, `FAILED`, "
            "or `CANCELLED`.\n\n"
            "**Auth:** Course Creator or invited Staff Writer.\n\n"
            "**Prerequisites:** The import job must belong to the caller.\n\n"
            "**Important:** Jobs owned by another creator return 404 so import "
            "existence is not leaked."
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                response=CourseImportJobSerializer,
                description="Current import job state.",
                examples=[
                    OpenApiExample(name="Ready for review", value=JOB_EXAMPLE),
                    OpenApiExample(
                        name="Failed CSV parse",
                        value={
                            **JOB_EXAMPLE,
                            "status": "FAILED",
                            "stage": "Import failed",
                            "error_message": (
                                "This CSV could not be parsed. Check the file formatting and retry."
                            ),
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
        tags=COURSE_IMPORT_TAG,
    )
    def get(self, request, pk):
        return Response(CourseImportJobSerializer(self.get_object(request, pk)).data)

    @extend_schema(
        summary="Cancel a document import",
        description=(
            "Cancels a creator-owned document import job. Terminal jobs are returned "
            "unchanged, so repeated cancellation is safe.\n\n"
            "Call this when the creator backs out of the document import flow.\n\n"
            "**Auth:** Course Creator or invited Staff Writer.\n\n"
            "**Prerequisites:** The import job must belong to the caller.\n\n"
            "**Important:** Cancellation does not delete the uploaded object from "
            "storage; it only stops this import workflow."
        ),
        request=None,
        responses={
            202: OpenApiResponse(
                response=CourseImportJobSerializer,
                description="Import cancelled or already terminal.",
                examples=[
                    OpenApiExample(
                        name="Cancelled",
                        value={
                            **JOB_EXAMPLE,
                            "status": "CANCELLED",
                            "stage": "Import cancelled",
                            "cancel_requested": True,
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
        tags=COURSE_IMPORT_TAG,
    )
    def delete(self, request, pk):
        job = course_import_service.cancel_import_job(
            job=self.get_object(request, pk), actor=request.user
        )
        return Response(CourseImportJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class CourseImportConfirmView(APIView):
    permission_classes = [IsCourseCreatorRole]
    serializer_class = CourseImportConfirmSerializer

    def get_object(self, request, pk):
        return CourseImportJob.objects.select_related("course", "category", "topic").filter(
            pk=pk, creator=request.user
        ).first() or (_ for _ in ()).throw(exceptions.NotFound("Import job not found."))

    @extend_schema(
        summary="Confirm a document import",
        description=(
            "Creates a Draft course from a reviewed document-import structure. "
            "Modules, lessons, lesson scripts, and lesson content blocks are "
            "created atomically from the reviewed tree.\n\n"
            "Call this after the frontend shows the detected module/lesson preview "
            "and the creator confirms the structure.\n\n"
            "**Auth:** Course Creator or invited Staff Writer.\n\n"
            "**Prerequisites:** The job must belong to the caller and be "
            "`READY_FOR_REVIEW`.\n\n"
            "**Important:** This action is not repeatable. After success the job "
            "moves to `COMPLETED` and later confirm attempts return a validation "
            "error."
        ),
        request=CourseImportConfirmSerializer,
        examples=[
            OpenApiExample(
                name="Confirm edited structure",
                request_only=True,
                value={
                    "structure": {
                        "course": {"title": "Python Basics from CSV"},
                        "modules": JOB_EXAMPLE["detected_structure"]["modules"],
                    }
                },
            )
        ],
        responses={
            200: OpenApiResponse(
                response=CourseImportConfirmResponseSerializer,
                description="Draft course created.",
                examples=[
                    OpenApiExample(
                        name="Course created",
                        value={
                            "course_id": "3f9a2e11-6b7c-4d2a-9e5f-1c8d4a7b2f30",
                            "status": "DRAFT",
                            "builder_url": "/courses/3f9a2e11-6b7c-4d2a-9e5f-1c8d4a7b2f30/builder",
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
        tags=COURSE_IMPORT_TAG,
    )
    def post(self, request, pk):
        serializer = CourseImportConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = course_import_service.confirm_import_job(
            job=self.get_object(request, pk),
            actor=request.user,
            structure=(serializer.validated_data.get("structure") or None),
        )
        return Response(
            {
                "course_id": course.id,
                "status": course.status,
                "builder_url": f"/courses/{course.id}/builder",
            }
        )
