from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import filters as drf_filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from api.collaborators.services import collaborator_service
from api.courses.enums import CourseStatus
from api.courses.filters import CourseReviewQueueFilter
from api.courses.models import Course
from api.courses.permissions import IsCourseOwner
from api.courses.serializers import (
    CourseCreateSerializer,
    CourseDetailSerializer,
    CourseListSerializer,
    CourseUpdateSerializer,
    ReviewActionSerializer,
    ReviewApproveSerializer,
    ReviewRejectSerializer,
)
from api.courses.services import course_service, review_service
from api.users.permissions import (
    IsAdminRole,
    IsCourseCreatorRole,
    IsCreatorReviewerRole,
)
from api.users.services import queue_preference_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

OWNER_SCOPED_ACTIONS = {"retrieve", "update", "partial_update", "destroy"}

#: Creators reach their own courses; Admins reach any course. Composed rather
#: than listed side by side because DRF ANDs a permission list together, which
#: would require an Admin to also hold the creator role.
OWNER_OR_ADMIN = (IsCourseCreatorRole & IsCourseOwner) | IsAdminRole

#: Submission is the one owner-scoped action an Admin does NOT get a bypass on.
#: Submitting is the author vouching for their own work (see
#: course_service.submit_course), so an Admin may submit only a course they
#: themselves created - in which case IsCourseOwner passes anyway.
SUBMIT_PERMISSION = (IsCourseCreatorRole | IsAdminRole) & IsCourseOwner

_COURSE_LIST_EXAMPLE = {
    "id": "3f9a2e11-6b7c-4d2a-9e5f-1c8d4a7b2f30",
    "title": "Intro to Python",
    "category": {
        "id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
        "name": "Software Engineering",
    },
    "topic": None,
    "status": "DRAFT",
    "creator_price_snapshot": None,
    "submitted_at": None,
    "created_datetime": "2026-07-12T09:30:11.204Z",
}

_COURSE_DETAIL_EXAMPLE = {
    **_COURSE_LIST_EXAMPLE,
    "description": "A hands-on introduction to Python for beginners.",
    "difficulty_level": "BEGINNER",
    "learning_objectives": ["Write basic Python scripts", "Understand data types"],
    "tags": ["python", "beginner"],
    "planned_duration_seconds": 7200,
    "preview_video_url": "",
    "thumbnail_url": "",
    "terms_accepted_at": "2026-07-12T09:30:11.204Z",
    "approved_at": None,
    "published_at": None,
    "rejected_at": None,
    "modules": [],
    "final_assessment": None,
    "duration_estimate_minutes": 120,
    "version": "1.0",
    "updated_datetime": "2026-07-12T09:30:11.204Z",
}

_REVIEW_ACTION_EXAMPLE = {
    "id": "9a1c3e5f-2b4d-4a6e-8f0c-3d5e7a9b1c2d",
    "course": "3f9a2e11-6b7c-4d2a-9e5f-1c8d4a7b2f30",
    "reviewer": {
        "id": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
        "email": "reviewer@example.com",
    },
    "action": "APPROVE",
    "feedback": {},
    "created_datetime": "2026-07-15T14:02:33.001Z",
}

_AUTH_LINE_COURSE = (
    "**Auth:** Course Creator or Writer (own courses only), or Admin/Approver "
    "(any course). A collaborator added to a course can also read/edit it "
    "(see [api/collaborators/](../collaborators/))."
)


@extend_schema_view(
    list=extend_schema(
        summary="List courses",
        description=(
            "Returns the caller's own courses (creator or collaborator), or "
            "every course on the platform for an Admin. This is the table "
            "behind the creator's 'My Courses' screen and the admin course "
            "list.\n\n"
            "Called when the My Courses screen loads, or the admin course "
            "table.\n\n"
            f"{_AUTH_LINE_COURSE}\n\n"
            "**Prerequisites:** None beyond holding the Course Creator, "
            "Writer, or Admin role.\n\n"
            "**Important:** Use `CourseReviewViewSet` "
            "(`GET /review-queue/`) instead for the reviewer queue - this "
            "endpoint never returns courses the caller doesn't own or "
            "collaborate on unless they are an Admin. Results are paginated."
        ),
        tags=["Courses"],
        responses={
            200: OpenApiResponse(
                response=CourseListSerializer(many=True),
                description="Courses, most recently created first.",
                examples=[OpenApiExample(name="Success", value=[_COURSE_LIST_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a course",
        description=(
            "Returns a single course with its full nested tree - modules, "
            "lessons, and any assessments already attached.\n\n"
            "Called when opening a course's builder/detail view.\n\n"
            f"{_AUTH_LINE_COURSE}\n\n"
            "**Prerequisites:** The course must exist and be visible to the "
            "caller.\n\n"
            "**Important:** A course the caller can't access 404s rather than "
            "403s, so existence isn't leaked."
        ),
        tags=["Courses"],
        responses={
            200: OpenApiResponse(
                response=CourseDetailSerializer,
                description="The requested course.",
                examples=[OpenApiExample(name="Success", value=_COURSE_DETAIL_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Create a draft course",
        description=(
            "Creates a new Draft course in the given category (and, "
            "optionally, topic). This is step one of authoring - modules, "
            "lessons, and assessments are added afterwards, and nothing is "
            "priced yet: `creator_price_snapshot` is only set at submit "
            "time.\n\n"
            "Called from the 'Create course' action on the My Courses "
            "screen.\n\n"
            "**Auth:** Course Creator or Writer.\n\n"
            "**Prerequisites:** `terms_accepted` must be `true` (BR-005) and "
            "the target category must be `ACTIVE`.\n\n"
            "**Important:** If `topic` is supplied it must belong to the "
            "chosen `category`, or the request 400s. Selecting an available "
            "topic reserves it for the creator immediately - same expiry "
            "window as an approved topic-reservation request - and the "
            "request 400s if the topic is currently reserved by someone "
            "else. `duration_hours`/`duration_minutes`/`duration_seconds` "
            "are write-only inputs combined into `planned_duration_seconds` "
            "on the stored course."
        ),
        tags=["Courses"],
        request=CourseCreateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "category": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
                    "title": "Intro to Python",
                    "description": "A hands-on introduction to Python for beginners.",
                    "difficulty_level": "BEGINNER",
                    "learning_objectives": ["Write basic Python scripts"],
                    "tags": ["python", "beginner"],
                    "duration_hours": 2,
                    "terms_accepted": True,
                },
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=CourseDetailSerializer,
                description="Course created as Draft.",
                examples=[OpenApiExample(name="Success", value=_COURSE_DETAIL_EXAMPLE)],
            ),
            400: OpenApiResponse(
                description=(
                    "Terms not accepted, the category isn't accepting "
                    "submissions, the topic doesn't belong to the category, "
                    "or the topic is currently reserved by someone else."
                ),
                examples=[
                    OpenApiExample(
                        name="Terms not accepted",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "You must accept the category Terms "
                                        "and Conditions to create a course."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Topic/category mismatch",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "topic does not belong to the "
                                        "selected category."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Topic already reserved",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "This topic is currently reserved.",
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    update=extend_schema(
        summary="Replace a draft course",
        description=(
            "Overwrites a Draft course's editable fields. Send the full "
            "object; prefer PATCH for routine edits.\n\n"
            "Called from the course edit form when every field is being "
            "submitted.\n\n"
            f"{_AUTH_LINE_COURSE}\n\n"
            "**Prerequisites:** The course must be `DRAFT`.\n\n"
            "**Important:** Editing anything other than a Draft course "
            "returns 400 - resubmission after rejection means the course is "
            "already back in Draft, so no separate 'unlock' step exists."
        ),
        tags=["Courses"],
        request=CourseUpdateSerializer,
        responses={
            200: OpenApiResponse(
                response=CourseDetailSerializer,
                description="Course updated.",
                examples=[OpenApiExample(name="Success", value=_COURSE_DETAIL_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    partial_update=extend_schema(
        summary="Update a draft course",
        description=(
            "Updates only the fields supplied - the normal way to edit a "
            "course while authoring it.\n\n"
            "Called from the course builder as the creator edits fields.\n\n"
            f"{_AUTH_LINE_COURSE}\n\n"
            "**Prerequisites:** The course must be `DRAFT`.\n\n"
            "**Important:** Supplying `duration_hours`/`duration_minutes`/"
            "`duration_seconds` recombines all three into "
            "`planned_duration_seconds` (missing ones treated as 0 for that "
            "call); omitting all three leaves the stored duration untouched."
        ),
        tags=["Courses"],
        request=CourseUpdateSerializer,
        examples=[
            OpenApiExample(
                name="Rename",
                request_only=True,
                value={"title": "Intro to Python (Updated)"},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=CourseDetailSerializer,
                description="Course updated.",
                examples=[OpenApiExample(name="Success", value=_COURSE_DETAIL_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    destroy=extend_schema(
        summary="Delete a draft course",
        description=(
            "Permanently deletes a Draft course and everything beneath it "
            "(modules, lessons, assessments). There is no undo.\n\n"
            "Called from the delete action on the My Courses screen.\n\n"
            f"{_AUTH_LINE_COURSE}\n\n"
            "**Prerequisites:** The course must be `DRAFT`.\n\n"
            "**Important:** A submitted/approved/published course cannot be "
            "deleted this way - there is deliberately no destructive path "
            "once a course has entered review."
        ),
        tags=["Courses"],
        responses={
            204: OpenApiResponse(description="Course deleted."),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class CourseViewSet(ModelViewSet):
    """Course authoring and submission (SCCS PRD Track A).

    Serves two audiences through one viewset: a Course Creator (or invited
    Writer) working on their own courses, and an Admin (or invited Approver)
    with full CRUD across every course on the platform. The queryset and
    object-level permissions branch on that distinction rather than the
    viewset being duplicated, because the request/response shapes are identical
    - only the visible scope differs.

    Admin access widens *who* may act, not *what* the workflow allows: the
    service layer's status rules still apply, so an Admin editing a Published
    course gets the same "Only Draft courses can be edited" error a creator
    would.
    """

    permission_classes = [IsCourseCreatorRole | IsAdminRole]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Course.objects.none()

        if self.action == "publish":
            # Admin-only (enforced by get_permissions) and not owner-scoped:
            # an Admin publishing a course is never its creator.
            return Course.objects.select_related("category", "topic")

        is_admin = IsAdminRole().has_permission(self.request, self)

        if is_admin:
            # Admins have full read/write visibility across every course.
            return Course.objects.select_related("category", "topic")

        if self.action in {"retrieve", "update", "partial_update"}:
            return collaborator_service.get_courses_accessible_to(
                self.request.user
            ).select_related("category", "topic")
        return Course.objects.select_related("category", "topic").filter(
            creator=self.request.user
        )

    def get_serializer_class(self):
        if self.action == "create":
            return CourseCreateSerializer
        if self.action in {"update", "partial_update"}:
            return CourseUpdateSerializer
        if self.action == "list":
            return CourseListSerializer
        return CourseDetailSerializer

    def get_permissions(self):
        if self.action in OWNER_SCOPED_ACTIONS:
            return [OWNER_OR_ADMIN()]
        if self.action == "submit":
            return [SUBMIT_PERMISSION()]
        if self.action == "publish":
            return [IsAdminRole()]
        if self.action == "create":
            return [IsCourseCreatorRole()]
        return super().get_permissions()

    def perform_destroy(self, instance):
        course_service.delete_draft_course(course=instance, actor=self.request.user)

    @extend_schema(
        summary="Submit a course for review",
        description=(
            "Transitions a Draft course to Submitted and snapshots the "
            "current category/topic price onto `creator_price_snapshot` - "
            "the price the creator is paid on approval is fixed at this "
            "moment, not at draft creation or at approval time.\n\n"
            "Called from the 'Submit for review' action once the creator "
            "believes the course is complete.\n\n"
            "**Auth:** The course's own Course Creator/Writer, or an Admin "
            "submitting a course they themselves created (an Admin cannot "
            "submit someone else's course - submission is the author "
            "vouching for their own work).\n\n"
            "**Prerequisites:** The course must be `DRAFT` and pass "
            "structural validation (minimum modules/lessons, learning "
            "objectives, assessments, etc. per SCCS PRD structural "
            "standards).\n\n"
            "**Important:** Structural validation failures are returned as "
            "an aggregated list under `structural_standards`, not one "
            "field at a time, so the UI can show every outstanding issue at "
            "once."
        ),
        tags=["Courses"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=CourseDetailSerializer,
                description="Course submitted.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={**_COURSE_DETAIL_EXAMPLE, "status": "SUBMITTED"},
                    )
                ],
            ),
            400: OpenApiResponse(
                description=("The course isn't Draft, or fails structural validation."),
                examples=[
                    OpenApiExample(
                        name="Not a draft",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "Course cannot be submitted from "
                                        "status 'SUBMITTED'."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Fails structural standards",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "['At least 1 module is required.', "
                                        "'Each lesson needs 2-5 learning "
                                        "objectives.']"
                                    ),
                                    "field_name": "structural_standards",
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
    def submit(self, request, pk=None):
        course = course_service.submit_course(
            course=self.get_object(), actor=request.user
        )
        return Response(
            CourseDetailSerializer(course, context=self.get_serializer_context()).data
        )

    @extend_schema(
        summary="Publish an approved course",
        description=(
            "Transitions an Approved course to Published, making it live. "
            "There is no external LMS push yet - this only flips the "
            "course's own status.\n\n"
            "Called from the 'Publish' action on the admin course detail "
            "view, after review has already approved the course.\n\n"
            "**Auth:** Admin only.\n\n"
            "**Prerequisites:** The course must be `APPROVED`.\n\n"
            "**Important:** Publishing is not reversible through this API - "
            "there is no unpublish action."
        ),
        tags=["Courses"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=CourseDetailSerializer,
                description="Course published.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={**_COURSE_DETAIL_EXAMPLE, "status": "PUBLISHED"},
                    )
                ],
            ),
            400: OpenApiResponse(
                description="The course isn't Approved.",
                examples=[
                    OpenApiExample(
                        name="Not approved",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "Course cannot be published from "
                                        "status 'DRAFT'."
                                    ),
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
    def publish(self, request, pk=None):
        course = course_service.publish_course(
            course=self.get_object(), actor=request.user
        )
        return Response(
            CourseDetailSerializer(course, context=self.get_serializer_context()).data
        )


@extend_schema_view(
    list=extend_schema(
        summary="List the reviewer queue",
        description=(
            "Returns courses a Creator Reviewer/Verifier or Admin needs to "
            "see: Submitted and In Review (the actual queue), plus Approved "
            "and Published for context, oldest-submitted-first. This is the "
            "table behind the review queue screen.\n\n"
            "Called when the review queue screen loads.\n\n"
            "**Auth:** Creator Reviewer, Verifier, or Admin.\n\n"
            "**Prerequisites:** None beyond holding one of those roles.\n\n"
            "**Important:** Narrow with `?status=SUBMITTED` (or any "
            "`CourseStatus` value) to show only one stage. Results are "
            "paginated and ordered by `submitted_at` ascending, so the "
            "oldest-waiting course is always first."
        ),
        tags=["Courses — Review Queue"],
        responses={
            200: OpenApiResponse(
                response=CourseListSerializer(many=True),
                description="Courses awaiting or recently through review.",
                examples=[OpenApiExample(name="Success", value=[_COURSE_LIST_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a course from the review queue",
        description=(
            "Returns a single course's full detail for the reviewer to "
            "inspect before deciding to claim/approve/reject it.\n\n"
            "Called when a reviewer opens a course from the queue.\n\n"
            "**Auth:** Creator Reviewer, Verifier, or Admin.\n\n"
            "**Prerequisites:** The course must exist.\n\n"
            "**Important:** Any course id is retrievable here regardless of "
            "status, so acting on it in the wrong status produces a 400 from "
            "the action endpoint rather than a misleading 404 at retrieve "
            "time."
        ),
        tags=["Courses — Review Queue"],
        responses={
            200: OpenApiResponse(
                response=CourseDetailSerializer,
                description="The requested course.",
                examples=[OpenApiExample(name="Success", value=_COURSE_DETAIL_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class CourseReviewViewSet(ReadOnlyModelViewSet):
    """Reviewer queue for Creator Track courses (SCCS PRD Section 11).

    Kept separate from CourseViewSet rather than branching one viewset's
    queryset by role, so a creator's own-courses queryset and a reviewer's
    cross-creator queue never share permission/queryset logic. `list` covers
    every stage a reviewer needs to browse - Submitted/In Review (the actual
    "queue"), plus Approved and Published - narrowable via
    CourseReviewQueueFilter's ?status= param; detail actions
    (retrieve/claim/approve/reject) look up any course by id, so acting on a
    course in the wrong status produces a 400 from the service layer rather
    than a misleading 404.
    """

    permission_classes = [IsCreatorReviewerRole | IsAdminRole]
    filterset_class = CourseReviewQueueFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["submitted_at"]
    #: Deliberately no `ordering` class default: OrderingFilter re-applies
    #: `.order_by()` from this attribute whenever `?ordering=` is absent from
    #: the request, which would silently overwrite the reviewer's stored
    #: QueueBehaviourPreference sort order applied in get_queryset() below.
    #: With no default, OrderingFilter only acts when `?ordering=` is
    #: explicitly passed - which is exactly when it should override the
    #: stored preference.

    def get_queryset(self):
        if self.action == "list":
            # An explicit ?ordering=/?track= query param always wins over the
            # reviewer's stored preference for that one axis - each is
            # checked independently so overriding one doesn't disable the
            # other's stored default.
            sort_order = None
            track_filter = None
            if (
                "ordering" not in self.request.query_params
                or "track" not in self.request.query_params
            ):
                preference = queue_preference_service.get_or_create_preference(
                    user=self.request.user
                )
                if "ordering" not in self.request.query_params:
                    sort_order = preference.default_sort_order
                if "track" not in self.request.query_params:
                    track_filter = preference.track_filter
            return course_service.get_review_queue(
                status_in=[
                    CourseStatus.SUBMITTED,
                    CourseStatus.IN_REVIEW,
                    CourseStatus.APPROVED,
                    CourseStatus.PUBLISHED,
                ],
                sort_order=sort_order,
                track_filter=track_filter,
                sla_user=self.request.user,
            )
        return Course.objects.select_related("category", "creator")

    def get_serializer_class(self):
        if self.action == "list":
            return CourseListSerializer
        if self.action == "approve":
            return ReviewApproveSerializer
        if self.action == "reject":
            return ReviewRejectSerializer
        return CourseDetailSerializer

    @extend_schema(
        summary="Claim a course for review",
        description=(
            "Transitions a Submitted course to In Review, marking that a "
            "specific reviewer is now working on it.\n\n"
            "Called when a reviewer opens a Submitted course and starts "
            "reviewing it.\n\n"
            "**Auth:** Creator Reviewer, Verifier, or Admin.\n\n"
            "**Prerequisites:** The course must be `SUBMITTED` or already "
            "`IN_REVIEW`; the reviewer must not be marked Unavailable.\n\n"
            "**Important:** Idempotent when the course is already "
            "`IN_REVIEW` - calling claim again succeeds as a no-op instead "
            "of erroring, so two reviewers racing to claim don't fail each "
            "other. A reviewer who has since gone Unavailable can still "
            "re-claim a course they already hold; only a *new* claim is "
            "blocked."
        ),
        tags=["Courses — Review Queue"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=CourseDetailSerializer,
                description="Course claimed (or already in review).",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={**_COURSE_DETAIL_EXAMPLE, "status": "IN_REVIEW"},
                    )
                ],
            ),
            400: OpenApiResponse(
                description="The course isn't Submitted/In Review.",
                examples=[
                    OpenApiExample(
                        name="Wrong status",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "Course cannot be claimed from "
                                        "status 'APPROVED'."
                                    ),
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
    def claim(self, request, pk=None):
        course = course_service.claim_for_review(
            course=self.get_object(), reviewer=request.user
        )
        return Response(
            CourseDetailSerializer(course, context=self.get_serializer_context()).data
        )

    @extend_schema(
        summary="Approve a course under review",
        description=(
            "Approves a Submitted/In Review course: records a ReviewAction, "
            "moves the course to Approved, credits the creator's wallet with "
            "the price snapshotted at submission, and notifies the "
            "creator - all atomically.\n\n"
            "Called from the 'Approve' action on the review screen.\n\n"
            "**Auth:** Creator Reviewer, Verifier, or Admin.\n\n"
            "**Prerequisites:** The course must be `SUBMITTED` or "
            "`IN_REVIEW`; the reviewer must not be marked Unavailable.\n\n"
            "**Important:** This is what pays the creator - the wallet "
            "credit uses `creator_price_snapshot`, frozen at submit time, "
            "never the category/topic's current price. `feedback` is "
            "optional here (unlike reject, where a summary is required)."
        ),
        tags=["Courses — Review Queue"],
        request=ReviewApproveSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={"feedback": {"summary": "Looks great, approved as-is."}},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=ReviewActionSerializer,
                description="Course approved.",
                examples=[OpenApiExample(name="Success", value=_REVIEW_ACTION_EXAMPLE)],
            ),
            400: OpenApiResponse(
                description="The course isn't Submitted/In Review.",
                examples=[
                    OpenApiExample(
                        name="Wrong status",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "Course cannot be approved from "
                                        "status 'DRAFT'."
                                    ),
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
        serializer = ReviewApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review_action = review_service.approve_course(
            course=self.get_object(),
            reviewer=request.user,
            feedback=serializer.validated_data["feedback"],
        )
        return Response(ReviewActionSerializer(review_action).data)

    @extend_schema(
        summary="Reject a course under review",
        description=(
            "Rejects a Submitted/In Review course: records a ReviewAction "
            "and reverts the course directly to Draft so the creator can "
            "revise and resubmit (per PRD 'Returns to Draft. Creator "
            "revises.'). No wallet credit occurs.\n\n"
            "Called from the 'Reject' action on the review screen.\n\n"
            "**Auth:** Creator Reviewer, Verifier, or Admin.\n\n"
            "**Prerequisites:** The course must be `SUBMITTED` or "
            "`IN_REVIEW`; the reviewer must not be marked Unavailable; "
            "`feedback.summary` must be a non-empty string.\n\n"
            "**Important:** Unlike approve, `feedback.summary` is required "
            "(US-202: reviewers must leave actionable feedback). "
            "`feedback.items`, if supplied, must be a list of objects each "
            "with `module_id` and `comment`. `CourseStatus.REJECTED` is "
            "never persisted on `Course.status` - the rejection is recorded "
            "via the returned ReviewAction and `Course.rejected_at` while "
            "the course itself shows `DRAFT`."
        ),
        tags=["Courses — Review Queue"],
        request=ReviewRejectSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "feedback": {
                        "summary": "Needs at least 2 modules and clearer objectives.",
                        "items": [
                            {
                                "module_id": "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
                                "comment": "Add a learning objective here.",
                            }
                        ],
                    }
                },
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=ReviewActionSerializer,
                description="Course rejected and reverted to Draft.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={**_REVIEW_ACTION_EXAMPLE, "action": "REJECT"},
                    )
                ],
            ),
            400: OpenApiResponse(
                description="Missing feedback summary, or the course isn't Submitted/In Review.",
                examples=[
                    OpenApiExample(
                        name="Missing summary",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "A summary is required when "
                                        "rejecting a course."
                                    ),
                                    "field_name": "feedback",
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Wrong status",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "Course cannot be rejected from "
                                        "status 'PUBLISHED'."
                                    ),
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
        serializer = ReviewRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review_action = review_service.reject_course(
            course=self.get_object(),
            reviewer=request.user,
            feedback=serializer.validated_data["feedback"],
        )
        return Response(ReviewActionSerializer(review_action).data)
