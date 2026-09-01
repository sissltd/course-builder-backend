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
from api.courses.filters import AdminCourseFilter, CourseFilter, CourseReviewQueueFilter
from api.courses.models import Course
from api.reviews.models import MediaAsset, ReviewComment
from api.courses.permissions import IsCourseOwner
from api.courses.serializers import (
    CourseCreateSerializer,
    CourseDetailSerializer,
    CourseListSerializer,
    CourseUpdateSerializer,
    CourseDistributionSerializer,
    ReviewAndPublishSerializer,
    ReviewApproveSerializer,
    ReviewRejectSerializer,
    QAApprovalSerializer,
    QARejectSerializer,
    ReviewCommentCreateSerializer,
    ReviewCommentSerializer,
    MediaAssetSerializer,
)
from api.courses.services import course_service
from api.reviews.serializers import ReviewActionSerializer
from api.reviews.services import review_service
from api.users.permissions import (
    IsAdminRole,
    IsCourseCreatorRole,
    IsCreatorReviewerRole,
    IsQaReviewerRole,
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
    "source": "CREATOR",
    "status": "DRAFT",
    "creator_price_snapshot": None,
    "submitted_at": None,
    "created_datetime": "2026-07-12T09:30:11.204Z",
    "updated_datetime": "2026-07-12T09:30:11.204Z",
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
    "version": "2f9a1e4b-7c8d-4a6e-9f0c-2d3e4f5a6b7c",
    "updated_datetime": "2026-07-12T09:30:11.204Z",
}

_REVIEW_PRICES_REQUEST_EXAMPLE = {
    "distribution_channels": [
        {
            "channel": "SOLUDESK",
            "approval_rate": "Published within 60 seconds",
            "learner_price": "149.00",
            "mie_suggestion": "140.00",
            "model": "ONE_TIME",
            "platform_revenue_per_enrollment": "149.00",
            "mie_explanation": (
                "$149 is the MIE-suggested price based on competitor analysis "
                "across Udemy and Coursera."
            ),
            "comparable_courses": [
                {
                    "course_title": "Modern computing language",
                    "difficulty_level": "BEGINNER",
                    "learner_price": "150.00",
                }
            ],
        },
        {
            "channel": "COURSERA",
            "approval_rate": "Published within 10 - 15 minutes",
            "learner_price": "160.00",
            "mie_suggestion": "100.00",
            "model": "ONE_TIME",
            "course_fee_percent": "32.00",
            "promotional_pricing": "150.00",
            "platform_revenue_per_enrollment": "149.00",
            "comparable_courses": [],
        },
        {
            "channel": "UDEMY",
            "approval_rate": "Published within 10 - 15 minutes",
            "learner_price": "190.00",
            "mie_suggestion": "100.00",
            "model": "ONE_TIME",
            "course_fee_percent": "32.00",
            "promotional_pricing": "150.00",
            "platform_revenue_per_enrollment": "149.00",
            "comparable_courses": [],
        },
    ]
}

_REVIEW_PRICE_RESPONSE_EXAMPLE = {
    "id": "04a546a5-16f2-4e54-afd5-547f4dfab301",
    "channel": "SOLUDESK",
    "approval_rate": "Published within 60 seconds",
    "learner_price": "149.00",
    "mie_suggestion": "140.00",
    "model": "ONE_TIME",
    "learner_fee": "149.00",
    "creator_payout_fixed": "150.00",
    "course_fee_percent": None,
    "promotional_pricing": None,
    "platform_revenue_per_enrollment": "149.00",
    "mie_explanation": "$149 is the MIE-suggested price based on competitor analysis.",
    "comparable_courses": [],
    "status": "DRAFT",
    "external_course_id": "",
    "failure_reason": "",
    "published_at": None,
}

_REVIEW_ACTION_EXAMPLE = {
    "id": "9a1c3e5f-2b4d-4a6e-8f0c-3d5e7a9b1c2d",
    "course": "3f9a2e11-6b7c-4d2a-9e5f-1c8d4a7b2f30",
    "reviewer": {
        "id": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
        "email": "reviewer@example.com",
    },
    "action": "APPROVE",
    "stage": "CONTENT",
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
        tags=["Creator — Courses"],
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
        tags=["Creator — Courses"],
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
        tags=["Creator — Courses"],
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
        tags=["Creator — Courses"],
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
        tags=["Creator — Courses"],
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
        tags=["Creator — Courses"],
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
    filterset_class = CourseFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = [
        "title",
        "created_datetime",
        "updated_datetime",
        "submitted_at",
        "quality_score",
        "status",
    ]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Course.objects.none()

        if self.action in {"publish", "review_prices"}:
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
        if self.action in {"publish", "review_prices"}:
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
        tags=["Creator — Courses"],
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
        methods=["get"],
        summary="Review course prices",
        description=(
            "Returns the saved pricing cards displayed in the Figma Review modal. "
            "Each row is one tab: `SOLUDESK`, `COURSERA`, or `UDEMY`.\n\n"
            "Called when an Admin opens Review and publish from the Approved "
            "Courses table.\n\n"
            "**Auth:** Admin, Approver, or Super Admin.\n\n"
            "**Prerequisites:** The course must exist. Pricing rows are empty until "
            "they are saved with PUT.\n\n"
            "**Important:** `creator_payout_fixed` comes from the creator price "
            "snapshot and cannot be changed here. Marketplace publication status is "
            "reported per channel."
        ),
        tags=["Admin — Courses"],
        responses={
            200: OpenApiResponse(
                response=CourseDistributionSerializer(many=True),
                description="Pricing tabs for the course.",
                examples=[
                    OpenApiExample(
                        name="SoluDesk pricing tab",
                        value=[_REVIEW_PRICE_RESPONSE_EXAMPLE],
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @extend_schema(
        methods=["put"],
        summary="Save course prices",
        description=(
            "Saves the exact fields entered or displayed in the SoluDesk, Coursera, "
            "and Udemy tabs before publishing. Existing rows for supplied channels "
            "are updated; omitted channels are left unchanged.\n\n"
            "Called when the Admin selects Continue after reviewing the channel "
            "prices.\n\n"
            "**Auth:** Admin, Approver, or Super Admin.\n\n"
            "**Prerequisites:** The course must be `APPROVED`. At least one channel "
            "is required, and each channel may occur only once.\n\n"
            "**Important:** Money fields are decimal strings. `model` accepts "
            "`ONE_TIME`, `SUBSCRIPTION`, `PROMOTIONAL`, or `B2B_ONLY`. "
            "`course_fee_percent` and `promotional_pricing` apply primarily to "
            "Coursera and Udemy."
        ),
        tags=["Admin — Courses"],
        request=ReviewAndPublishSerializer,
        examples=[
            OpenApiExample(
                name="Three Figma pricing tabs",
                request_only=True,
                value=_REVIEW_PRICES_REQUEST_EXAMPLE,
            )
        ],
        responses={
            200: OpenApiResponse(
                response=CourseDistributionSerializer(many=True),
                description="Saved channel pricing.",
                examples=[
                    OpenApiExample(
                        name="Saved SoluDesk pricing",
                        value=[_REVIEW_PRICE_RESPONSE_EXAMPLE],
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
    @action(detail=True, methods=["get", "put"], url_path="review-prices")
    def review_prices(self, request, pk=None):
        course = self.get_object()
        if request.method == "GET":
            rows = course.distribution_channels.all()
        else:
            serializer = ReviewAndPublishSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            rows = course_service.save_distribution_channels(
                course=course,
                channels=serializer.validated_data["distribution_channels"],
            )
        return Response(CourseDistributionSerializer(rows, many=True).data)

    @extend_schema(
        summary="Publish an approved course",
        description=(
            "Confirms the Review and publish overview and transitions an Approved "
            "course to Published. Pricing may be supplied in this request or saved "
            "first through `PUT /courses/{id}/review-prices/`. SoluDesk is marked "
            "Published locally; Coursera and Udemy are marked Queued for their "
            "future integration workers.\n\n"
            "Called when the Admin presses Continue on the Review and publish "
            "overview modal.\n\n"
            "**Auth:** Admin, Approver, or Super Admin.\n\n"
            "**Prerequisites:** The course must be `APPROVED`. Existing clients may "
            "publish without pricing; the Figma workflow supplies or first saves at "
            "least one distribution channel.\n\n"
            "**Important:** This action is atomic and not reversible through this "
            "API. It creates the immutable published snapshot. A Queued marketplace "
            "row does not mean the external marketplace has accepted the course."
        ),
        tags=["Admin — Courses"],
        request=ReviewAndPublishSerializer,
        examples=[
            OpenApiExample(
                name="Review and publish all channels",
                request_only=True,
                value=_REVIEW_PRICES_REQUEST_EXAMPLE,
            )
        ],
        responses={
            200: OpenApiResponse(
                response=CourseDetailSerializer,
                description="Course published.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            **_COURSE_DETAIL_EXAMPLE,
                            "status": "PUBLISHED",
                            "distribution_channels": [
                                {
                                    **_REVIEW_PRICE_RESPONSE_EXAMPLE,
                                    "status": "PUBLISHED",
                                    "published_at": "2026-08-26T08:30:00Z",
                                }
                            ],
                        },
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
        course = self.get_object()
        distribution_channels = None
        if request.data:
            serializer = ReviewAndPublishSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            distribution_channels = serializer.validated_data["distribution_channels"]
        course = course_service.publish_course(
            course=course,
            actor=request.user,
            distribution_channels=distribution_channels,
        )
        return Response(
            CourseDetailSerializer(course, context=self.get_serializer_context()).data
        )

    @extend_schema(
        methods=["get"],
        summary="List course media assets",
        description=(
            "Returns the media inventory and technical evidence registered for a "
            "course. Creators use it to confirm what QA will evaluate.\n\n"
            "Called while preparing a course for QA verification.\n\n"
            "**Auth:** Course Creator (own course only) or Admin.\n\n"
            "**Prerequisites:** The course must exist and be accessible to the caller.\n\n"
            "**Important:** Course-level preview videos and thumbnails do not have a "
            "`lesson`; lesson media does."
        ),
        tags=["Creator — Courses"],
        responses={
            200: OpenApiResponse(
                response=MediaAssetSerializer(many=True),
                description="Registered media assets.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value=[
                            {
                                "id": "a2f1040c-6b9e-4b70-8a3d-9d590eff6d4b",
                                "lesson": "a6f7a05c-8942-40eb-9f6f-bcf988763e6d",
                                "kind": "VIDEO",
                                "url": "https://media.example.com/python-intro.mp4",
                                "mime_type": "video/mp4",
                                "duration_seconds": 300,
                                "resolution": "1920x1080",
                                "subtitle_url": "https://media.example.com/python-intro.vtt",
                            }
                        ],
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @extend_schema(
        methods=["post"],
        summary="Register a course media asset",
        description=(
            "Registers media metadata that the QA reviewer uses to verify a course's "
            "video, audio, caption, and accessibility requirements. It does not upload "
            "the file itself.\n\n"
            "Called after a media file has been uploaded and its accessible URL is known.\n\n"
            "**Auth:** Course Creator (own course only) or Admin.\n\n"
            "**Prerequisites:** The course must exist; any supplied `lesson` must belong "
            "to that course.\n\n"
            "**Important:** Register a `VIDEO` for each lesson and the required "
            "course-level preview/thumbnail assets before QA approval."
        ),
        tags=["Creator — Courses"],
        request=MediaAssetSerializer,
        examples=[
            OpenApiExample(
                name="Lesson video",
                request_only=True,
                value={
                    "lesson": "a6f7a05c-8942-40eb-9f6f-bcf988763e6d",
                    "kind": "VIDEO",
                    "url": "https://media.example.com/python-intro.mp4",
                    "mime_type": "video/mp4",
                    "duration_seconds": 300,
                    "resolution": "1920x1080",
                    "subtitle_url": "https://media.example.com/python-intro.vtt",
                    "caption_accuracy_percent": "99.00",
                    "audio_lufs": "-16.00",
                    "audio_video_drift_ms": 50,
                    "accessibility": {"captions": True},
                },
            )
        ],
        responses={
            201: OpenApiResponse(
                response=MediaAssetSerializer,
                description="Media asset registered.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "id": "a2f1040c-6b9e-4b70-8a3d-9d590eff6d4b",
                            "kind": "VIDEO",
                            "url": "https://media.example.com/python-intro.mp4",
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
    @action(detail=True, methods=["get", "post"], url_path="media-assets")
    def media_assets(self, request, pk=None):
        """Read or register the metadata that the QA stage verifies."""
        course = self.get_object()
        if request.method == "GET":
            return Response(
                MediaAssetSerializer(course.media_assets.all(), many=True).data
            )
        serializer = MediaAssetSerializer(data=request.data, context={"course": course})
        serializer.is_valid(raise_exception=True)
        asset = MediaAsset.objects.create(course=course, **serializer.validated_data)
        return Response(MediaAssetSerializer(asset).data, status=201)


@extend_schema_view(
    list=extend_schema(
        summary="List the reviewer queue",
        description=(
            "Returns courses awaiting content review or QA verification, plus Approved "
            "and Published for context, oldest-submitted-first. This is the "
            "table behind the review queue screen.\n\n"
            "Called when the review queue screen loads.\n\n"
            "**Auth:** Creator Reviewer, QA Reviewer, Verifier, or Admin.\n\n"
            "**Prerequisites:** None beyond holding one of those roles.\n\n"
            "**Important:** Narrow with `?status=SUBMITTED` (or any "
            "`CourseStatus` value) to show only one stage. Results are "
            "paginated and ordered by `submitted_at` ascending, so the "
            "oldest-waiting course is always first."
        ),
        tags=["Reviewer — Review Queue"],
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
        tags=["Reviewer — Review Queue"],
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
    """Two-stage course reviewer queue (SCCS PRD Sections 6 and 11).

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

    permission_classes = [IsCreatorReviewerRole | IsQaReviewerRole | IsAdminRole]
    filterset_class = CourseReviewQueueFilter
    filter_backends = [
        DjangoFilterBackend,
        drf_filters.SearchFilter,
        drf_filters.OrderingFilter,
    ]
    search_fields = [
        "id",
        "title",
        "creator__first_name",
        "creator__last_name",
        "creator__email",
    ]
    ordering_fields = ["submitted_at", "approved_at", "published_at"]
    #: Deliberately no `ordering` class default: OrderingFilter re-applies
    #: `.order_by()` from this attribute whenever `?ordering=` is absent from
    #: the request, which would silently overwrite the reviewer's stored
    #: QueueBehaviourPreference sort order applied in get_queryset() below.
    #: With no default, OrderingFilter only acts when `?ordering=` is
    #: explicitly passed - which is exactly when it should override the
    #: stored preference.

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Course.objects.none()
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
                    CourseStatus.QA_VERIFICATION,
                    CourseStatus.APPROVED,
                    CourseStatus.PUBLISHED,
                ],
                sort_order=sort_order,
                track_filter=track_filter,
                sla_user=self.request.user,
            )
        return Course.objects.select_related("category", "creator").prefetch_related(
            "modules__lessons__assessment",
            "modules__assessment",
            "final_assessment",
            "media_assets__verified_by",
            "media_assets__lesson__module",
            "quality_check_runs__findings",
            "quality_findings",
            "review_assignments__reviewer",
            "review_comments__reviewer",
        )

    def get_serializer_class(self):
        if self.action == "list":
            return CourseListSerializer
        if self.action in {"approve", "content_approve"}:
            return ReviewApproveSerializer
        if self.action in {"reject", "content_reject"}:
            return ReviewRejectSerializer
        if self.action == "qa_approve":
            return QAApprovalSerializer
        if self.action == "qa_reject":
            return QARejectSerializer
        if self.action == "comments":
            return ReviewCommentCreateSerializer
        return CourseDetailSerializer

    def get_permissions(self):
        if self.action in {"qa_claim", "qa_approve", "qa_reject"}:
            return [(IsQaReviewerRole | IsAdminRole)()]
        return [permission() for permission in self.permission_classes]

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
            "**Important:** Idempotent for the reviewer who already holds the "
            "assignment. A competing reviewer receives a validation error. "
            "A reviewer who has since gone Unavailable can still "
            "re-claim a course they already hold; only a *new* claim is "
            "blocked."
        ),
        tags=["Reviewer — Review Queue"],
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
            "Approves a Submitted/In Review course's content and moves it to "
            "mandatory QA verification. Creator payment occurs only after QA "
            "approval.\n\n"
            "Called from the 'Approve' action on the review screen.\n\n"
            "**Auth:** Creator Reviewer, Verifier, or Admin.\n\n"
            "**Prerequisites:** The course must be `SUBMITTED` or "
            "`IN_REVIEW`; the reviewer must not be marked Unavailable.\n\n"
            "**Important:** This does not publish or pay; it moves the course to "
            "the QA queue. `feedback` is optional here (unlike reject, where a "
            "summary is required)."
        ),
        tags=["Reviewer — Review Queue"],
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
                                        "Course cannot be approved from status 'DRAFT'."
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
        review_action = review_service.approve_content(
            course=self.get_object(),
            reviewer=request.user,
            feedback=serializer.validated_data["feedback"],
        )
        return Response(ReviewActionSerializer(review_action).data)

    @extend_schema(
        summary="Approve course content",
        description=(
            "Approves the course's written content and advances it to QA verification. "
            "This explicit route has the same behaviour as the standard approve action.\n\n"
            "Called when a content reviewer completes their review.\n\n"
            "**Auth:** Creator Reviewer, Verifier, or Admin.\n\n"
            "**Prerequisites:** The course must be `SUBMITTED` or `IN_REVIEW`.\n\n"
            "**Important:** This action does not approve payment or publication; it only "
            "moves the course to QA."
        ),
        tags=["Reviewer — Review Queue"],
        request=ReviewApproveSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={"feedback": {"summary": "Content is complete."}},
            )
        ],
        responses={
            200: OpenApiResponse(
                response=ReviewActionSerializer,
                description="Content approval recorded.",
                examples=[OpenApiExample(name="Success", value=_REVIEW_ACTION_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"], url_path="content-approve")
    def content_approve(self, request, pk=None):
        """Explicit alias for approve; approval advances the course to QA."""
        return self.approve(request, pk)

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
        tags=["Reviewer — Review Queue"],
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
                                        "A summary is required when rejecting a course."
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
            flags=serializer.validated_data.get("flags") or [],
        )
        return Response(ReviewActionSerializer(review_action).data)

    @extend_schema(
        summary="Reject course content",
        description=(
            "Rejects the course at the content-review gate and returns it to Draft for "
            "the creator to revise. This explicit route has the same behaviour as the "
            "standard reject action.\n\n"
            "Called when a content reviewer finds blocking issues.\n\n"
            "**Auth:** Creator Reviewer, Verifier, or Admin.\n\n"
            "**Prerequisites:** The course must be `SUBMITTED` or `IN_REVIEW`, and "
            "`feedback.summary` must be non-empty.\n\n"
            "**Important:** The rejection feedback is shown to the creator; make it "
            "specific and actionable."
        ),
        tags=["Reviewer — Review Queue"],
        request=ReviewRejectSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "feedback": {"summary": "Add learning objectives to module two."}
                },
            )
        ],
        responses={
            200: OpenApiResponse(
                response=ReviewActionSerializer,
                description="Content rejection recorded.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={**_REVIEW_ACTION_EXAMPLE, "action": "REJECT"},
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
    @action(detail=True, methods=["post"], url_path="content-reject")
    def content_reject(self, request, pk=None):
        return self.reject(request, pk)

    @extend_schema(
        summary="Claim a course for QA",
        description=(
            "Claims a course in QA verification for the authenticated QA reviewer. "
            "The assignment identifies who is accountable for the final quality gate.\n\n"
            "Called when a QA reviewer begins checking a course that passed content review.\n\n"
            "**Auth:** QA Reviewer or Admin.\n\n"
            "**Prerequisites:** The course must be in `QA_VERIFICATION` and the caller "
            "must be available.\n\n"
            "**Important:** A course already claimed by another QA reviewer cannot be "
            "claimed again."
        ),
        tags=["Reviewer — Review Queue"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=CourseDetailSerializer,
                description="Course claimed for QA.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={**_COURSE_DETAIL_EXAMPLE, "status": "QA_VERIFICATION"},
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
    @action(detail=True, methods=["post"], url_path="qa-claim")
    def qa_claim(self, request, pk=None):
        course = review_service.claim_qa_verification(
            course=self.get_object(), reviewer=request.user
        )
        return Response(
            CourseDetailSerializer(course, context=self.get_serializer_context()).data
        )

    @extend_schema(
        summary="Approve a course in QA",
        description=(
            "Completes the QA quality gate, approves the course, and credits the creator "
            "when the course originated from a creator.\n\n"
            "Called after the QA reviewer has verified all required media and quality checks.\n\n"
            "**Auth:** QA Reviewer or Admin.\n\n"
            "**Prerequisites:** The course must be `QA_VERIFICATION`, the caller must be "
            "available, and required media checks must pass.\n\n"
            "**Important:** This action creates the final approval and can credit the "
            "creator wallet; do not retry after a successful response."
        ),
        tags=["Reviewer — Review Queue"],
        request=QAApprovalSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "feedback": {
                        "summary": "Captions, audio, and preview assets verified."
                    }
                },
            )
        ],
        responses={
            200: OpenApiResponse(
                response=ReviewActionSerializer,
                description="QA approval recorded.",
                examples=[
                    OpenApiExample(
                        name="Success", value={**_REVIEW_ACTION_EXAMPLE, "stage": "QA"}
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
    @action(detail=True, methods=["post"], url_path="qa-approve")
    def qa_approve(self, request, pk=None):
        serializer = QAApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = review_service.approve_qa(
            course=self.get_object(),
            reviewer=request.user,
            feedback=serializer.validated_data["feedback"],
        )
        return Response(ReviewActionSerializer(action).data)

    @extend_schema(
        summary="Reject a course in QA",
        description=(
            "Rejects a course at the QA quality gate and returns it to Draft so the "
            "creator can correct media or accessibility issues.\n\n"
            "Called when required QA checks or media evidence fail.\n\n"
            "**Auth:** QA Reviewer or Admin.\n\n"
            "**Prerequisites:** The course must be `QA_VERIFICATION` and "
            "`feedback.summary` must be non-empty.\n\n"
            "**Important:** The feedback is delivered to the creator and should identify "
            "the precise media or accessibility correction required."
        ),
        tags=["Reviewer — Review Queue"],
        request=QARejectSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "feedback": {
                        "summary": "Add captions to lesson three and re-upload the preview video."
                    }
                },
            )
        ],
        responses={
            200: OpenApiResponse(
                response=ReviewActionSerializer,
                description="QA rejection recorded.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            **_REVIEW_ACTION_EXAMPLE,
                            "action": "REJECT",
                            "stage": "QA",
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
    @action(detail=True, methods=["post"], url_path="qa-reject")
    def qa_reject(self, request, pk=None):
        serializer = QARejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = review_service.reject_qa(
            course=self.get_object(),
            reviewer=request.user,
            feedback=serializer.validated_data["feedback"],
        )
        return Response(ReviewActionSerializer(action).data)

    @extend_schema(
        methods=["get"],
        summary="List course review comments",
        description=(
            "Returns comments recorded by content and QA reviewers for a course. These "
            "comments explain the issues the creator must address.\n\n"
            "Called when a reviewer or creator opens the course review history.\n\n"
            "**Auth:** Creator Reviewer, QA Reviewer, or Admin.\n\n"
            "**Prerequisites:** The course must exist.\n\n"
            "**Important:** Comments are returned across both review stages; inspect the "
            "`stage` field to distinguish them."
        ),
        tags=["Reviewer — Review Queue"],
        responses={
            200: OpenApiResponse(
                response=ReviewCommentSerializer(many=True),
                description="Review comments.",
                examples=[OpenApiExample(name="Success", value=[])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @extend_schema(
        methods=["post"],
        summary="Add a course review comment",
        description=(
            "Adds a reviewer comment to a course, optionally attached to one module or "
            "lesson. The comment becomes part of the review record returned to the client.\n\n"
            "Called while documenting a content or QA finding.\n\n"
            "**Auth:** Creator Reviewer, QA Reviewer, or Admin.\n\n"
            "**Prerequisites:** The course must exist; supplied module and lesson IDs must "
            "belong to it.\n\n"
            "**Important:** Comments are not automatically resolved when a course changes "
            "status; submit only actionable review notes."
        ),
        tags=["Reviewer — Review Queue"],
        request=ReviewCommentCreateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "stage": "QA",
                    "lesson": "a6f7a05c-8942-40eb-9f6f-bcf988763e6d",
                    "severity": "ERROR",
                    "reason_code": "MISSING_CAPTIONS",
                    "comment": "Upload accurate English captions for this lesson.",
                },
            )
        ],
        responses={
            201: OpenApiResponse(
                response=ReviewCommentSerializer,
                description="Review comment created.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "id": "cb1a0a11-65cc-444a-9f44-a9a2f0a6507f",
                            "stage": "QA",
                            "severity": "ERROR",
                            "reason_code": "MISSING_CAPTIONS",
                            "comment": "Upload accurate English captions for this lesson.",
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
    @action(detail=True, methods=["get", "post"])
    def comments(self, request, pk=None):
        course = self.get_object()
        if request.method == "GET":
            comments = course.review_comments.select_related("reviewer").all()
            return Response(ReviewCommentSerializer(comments, many=True).data)
        serializer = ReviewCommentCreateSerializer(
            data=request.data, context={"course": course}
        )
        serializer.is_valid(raise_exception=True)
        comment = ReviewComment.objects.create(
            course=course, reviewer=request.user, **serializer.validated_data
        )
        return Response(ReviewCommentSerializer(comment).data, status=201)


class AdminCourseViewSet(CourseReviewViewSet):
    """All-course Admin table with the existing two-stage review actions."""

    filterset_class = AdminCourseFilter
    ordering_fields = [
        "title",
        "created_datetime",
        "updated_datetime",
        "submitted_at",
        "approved_at",
        "published_at",
        "quality_score",
        "status",
    ]
    ordering = ["-created_datetime"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Course.objects.none()
        return Course.objects.select_related(
            "category", "topic", "creator"
        ).prefetch_related(
            "modules__lessons__assessment",
            "modules__assessment",
            "final_assessment",
            "media_assets__verified_by",
            "media_assets__lesson__module",
            "quality_check_runs__findings",
            "quality_findings",
            "review_assignments__reviewer",
            "review_comments__reviewer",
        )
