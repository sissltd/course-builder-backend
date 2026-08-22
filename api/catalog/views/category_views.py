from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import filters as drf_filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from api.catalog.enums import CategoryDeletionStrategy
from api.catalog.filters import CategoryFilter
from api.catalog.models import Category
from api.catalog.serializers import (
    CategoryDeletionImpactSerializer,
    CategoryDeletionSerializer,
    CategorySerializer,
    CategoryWriteSerializer,
)
from api.catalog.services import category_service
from api.users.permissions import CanManageCategories, IsMFAVerifiedForSession
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

WRITE_ACTIONS = {"create", "update", "partial_update", "destroy"}

_CATEGORY_EXAMPLE = {
    "id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
    "name": "Software Engineering",
    "description": "Courses covering programming, architecture, and delivery practices.",
    "creator_price": "150.00",
    "track_preference": "OPEN",
    "status": "ACTIVE",
    "created_datetime": "2026-07-12T09:30:11.204Z",
    "updated_datetime": "2026-07-19T06:04:45.882Z",
}

_AUTH_LINE = (
    "**Auth:** Writer, Admin, or Super Admin. Approvers, Reviewers, and public "
    "Course Creators have read access only — they browse categories but do not "
    "manage them."
)

_PRICE_WARNING = (
    "Editing `creator_price` is not retroactive: `Course.creator_price_snapshot` "
    "freezes the rate when a course is submitted, so a change here only affects "
    "courses submitted afterwards and never alters an existing payout."
)


@extend_schema_view(
    list=extend_schema(
        summary="List course categories",
        description=(
            "Returns every course category with its creator price, track "
            "preference, and whether it is currently accepting submissions. "
            "This is the lookup creators use to choose where a new course "
            "belongs, and the table behind the admin Categories screen.\n\n"
            "Called when rendering the category picker during course creation, "
            "and whenever the Categories screen loads.\n\n"
            "**Auth:** Any authenticated user. Everyone needs to read "
            "categories; only Writers and Super Admins can change them.\n\n"
            "**Prerequisites:** None beyond being signed in.\n\n"
            "**Important:** Includes `INACTIVE` categories, which must not be "
            "offered when creating a course — filter with `?status=ACTIVE` for "
            "a picker. Results are paginated."
        ),
        tags=["Creator — Categories"],
        responses={
            200: OpenApiResponse(
                response=CategorySerializer(many=True),
                description="Categories, ordered by name.",
                examples=[OpenApiExample(name="Success", value=[_CATEGORY_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a course category",
        description=(
            "Returns a single category by id, including its current creator "
            "price and submission status.\n\n"
            "Called when opening a category's detail or edit view.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None beyond being signed in.\n\n"
            "**Important:** None."
        ),
        tags=["Creator — Categories"],
        responses={
            200: OpenApiResponse(
                response=CategorySerializer,
                description="The requested category.",
                examples=[OpenApiExample(name="Success", value=_CATEGORY_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Create a course category",
        description=(
            "Creates a category that creators can then submit courses into, "
            "fixing the price paid for an approved course in it. New "
            "categories are `ACTIVE` unless told otherwise, so they become "
            "selectable immediately.\n\n"
            "Called from the 'Create category' action on the Categories "
            "screen.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** None beyond holding the Writer or Super Admin "
            "role.\n\n"
            f"**Important:** `name` must be unique — a duplicate returns 400. "
            f"{_PRICE_WARNING}"
        ),
        tags=["Admin — Categories"],
        request=CategoryWriteSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "name": "Software Engineering",
                    "description": (
                        "Courses covering programming, architecture, and "
                        "delivery practices."
                    ),
                    "creator_price": "150.00",
                    "track_preference": "OPEN",
                    "status": "ACTIVE",
                },
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=CategorySerializer,
                description="Category created.",
                examples=[OpenApiExample(name="Success", value=_CATEGORY_EXAMPLE)],
            ),
            400: OpenApiResponse(
                description="The name is taken or a field failed validation.",
                examples=[
                    OpenApiExample(
                        name="Duplicate name",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "unique",
                                    "message": (
                                        "category with this Name already exists."
                                    ),
                                    "field_name": "name",
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Negative price",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "min_value",
                                    "message": (
                                        "Ensure this value is greater than or "
                                        "equal to 0."
                                    ),
                                    "field_name": "creator_price",
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
        summary="Replace a course category",
        description=(
            "Overwrites a category's details. Send the full object; fields you "
            "omit are reset to their defaults or rejected as required. Prefer "
            "PATCH for routine edits.\n\n"
            "Called from the category edit dialog when every field is being "
            "submitted.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The category must exist.\n\n"
            f"**Important:** {_PRICE_WARNING} Closing a category to new work is "
            "a `status` change to `INACTIVE`, not a delete."
        ),
        tags=["Admin — Categories"],
        request=CategoryWriteSerializer,
        responses={
            200: OpenApiResponse(
                response=CategorySerializer,
                description="Category updated.",
                examples=[OpenApiExample(name="Success", value=_CATEGORY_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    partial_update=extend_schema(
        summary="Update a course category",
        description=(
            "Updates only the fields supplied — the normal way to rename a "
            "category, adjust its price, or open and close it to submissions.\n\n"
            "Called from the category edit dialog and from the status toggle "
            "on the Categories screen.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The category must exist.\n\n"
            f"**Important:** {_PRICE_WARNING} Setting `status` to `INACTIVE` "
            "stops new submissions but leaves courses already in the category "
            "untouched — this is the safe alternative to deleting."
        ),
        tags=["Admin — Categories"],
        request=CategoryWriteSerializer,
        examples=[
            OpenApiExample(
                name="Close to new submissions",
                request_only=True,
                value={"status": "INACTIVE"},
            ),
            OpenApiExample(
                name="Reprice",
                request_only=True,
                value={"creator_price": "175.00"},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=CategorySerializer,
                description="Category updated.",
                examples=[OpenApiExample(name="Success", value=_CATEGORY_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    destroy=extend_schema(
        summary="Delete a course category",
        description=(
            "Deletes a category and decides what happens to the courses in it. "
            "A category with no courses is removed outright. A category that "
            "still holds courses needs a `strategy`, because the courses "
            "cannot simply be orphaned — either they move somewhere else or "
            "they go too.\n\n"
            "Called from the delete confirmation on the Categories screen, "
            "after `GET /categories/{id}/deletion-impact/` has told the admin "
            "how many courses are at stake and they have picked an option.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** When the category has courses, `strategy` is "
            "required, and `REASSIGN` additionally requires "
            "`replacement_category`. Call the deletion-impact endpoint first "
            "to know which applies.\n\n"
            "**Important:** Omitting `strategy` on a category that has courses "
            "returns **409**, not 400 — that is the signal to open the "
            "'what should happen to these courses?' dialog, and nothing is "
            "deleted. `DELETE_COURSES` is heavily destructive and not limited "
            "to drafts: it removes published courses and cascades to their "
            "modules, lessons, assessments, and review history, none of which "
            "can be recovered. `REASSIGN` preserves everything and does not "
            "change any payout — submitted courses keep the price they froze "
            "at submission. Under either strategy, onboarding profiles naming "
            "this category as their primary expertise silently lose that value. "
            "The whole operation is atomic. To retire a category without any "
            "of this, PATCH `status` to `INACTIVE` instead."
        ),
        tags=["Admin — Categories"],
        request=None,
        parameters=[
            OpenApiParameter(
                name="strategy",
                type=str,
                enum=[c.value for c in CategoryDeletionStrategy],
                required=False,
                description=(
                    "What to do with the category's courses. Required when the "
                    "category has any. `REASSIGN` moves them to "
                    "`replacement_category`; `DELETE_COURSES` deletes them and "
                    "everything beneath them."
                ),
            ),
            OpenApiParameter(
                name="replacement_category",
                type=str,
                required=False,
                description=(
                    "UUID of the category to move courses into. Required when "
                    "`strategy=REASSIGN`; must not be the category being "
                    "deleted."
                ),
            ),
        ],
        responses={
            204: OpenApiResponse(description="Category deleted."),
            400: OpenApiResponse(
                description=(
                    "The strategy or replacement category is unusable — a "
                    "missing replacement, or one pointing at the category "
                    "being deleted."
                ),
                examples=[
                    OpenApiExample(
                        name="Replacement missing",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "A replacement category is required "
                                        "when reassigning courses."
                                    ),
                                    "field_name": "replacement_category",
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Replacement is the category being deleted",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "Courses cannot be reassigned to the "
                                        "category being deleted."
                                    ),
                                    "field_name": "replacement_category",
                                }
                            ]
                        },
                    ),
                ],
            ),
            409: OpenApiResponse(
                description=(
                    "The category has courses and no strategy was given. "
                    "Nothing was deleted."
                ),
                examples=[
                    OpenApiExample(
                        name="Decision required",
                        value={
                            "errors": [
                                {
                                    "type": "client_error",
                                    "code": "category_deletion_needs_strategy",
                                    "message": (
                                        "'Software Engineering' still has 12 "
                                        "course(s). Choose whether to move them "
                                        "to another category or delete them."
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
    ),
)
class CategoryViewSet(ModelViewSet):
    """Staff-managed course categories (SCCS PRD Section 7).

    List/retrieve are open to any authenticated user - creators need to browse
    categories to pick one (US-101). Create/update/delete are restricted to
    Writers, Admins, and Super Admins via CanManageCategories; note this
    deliberately excludes Approvers, who read categories like everyone else.
    Writes also require an MFA-verified session (IsMFAVerifiedForSession) -
    categories carry creator_price, a financial policy change.
    """

    queryset = Category.objects.all()
    filterset_class = CategoryFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["name", "creator_price", "created_datetime"]

    def get_serializer_class(self):
        if self.action in WRITE_ACTIONS:
            return CategoryWriteSerializer
        if self.action == "deletion_impact":
            return CategoryDeletionImpactSerializer
        return CategorySerializer

    def get_permissions(self):
        if self.action in WRITE_ACTIONS:
            return [CanManageCategories(), IsMFAVerifiedForSession()]
        return super().get_permissions()

    @extend_schema(
        summary="Preview what deleting a category would affect",
        description=(
            "Reports how many courses live in a category, broken down by "
            "status, plus how many onboarding profiles name it as their "
            "primary expertise. Nothing is changed — this exists so the delete "
            "confirmation can warn the admin with real numbers and offer the "
            "right choice, instead of letting them find out afterwards.\n\n"
            "Called when the admin clicks delete on the Categories screen, "
            "before `DELETE /categories/{id}/` is sent with their decision.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The category must exist.\n\n"
            "**Important:** `requires_strategy` tells you whether the delete "
            "dialog needs to ask anything at all — when it is false, a plain "
            "DELETE succeeds. Treat the counts as a snapshot: a course created "
            "between this call and the delete is included in whatever strategy "
            "runs. `affected_creator_profile_count` is informational — those "
            "profiles are never deleted, they just lose the field, and that "
            "happens under both strategies."
        ),
        tags=["Admin — Categories"],
        responses={
            200: OpenApiResponse(
                response=CategoryDeletionImpactSerializer,
                description="What the deletion would affect.",
                examples=[
                    OpenApiExample(
                        name="Category with courses",
                        value={
                            "category_id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
                            "category_name": "Software Engineering",
                            "course_count": 12,
                            "courses_by_status": {
                                "DRAFT": 7,
                                "SUBMITTED": 2,
                                "PUBLISHED": 3,
                            },
                            "affected_creator_profile_count": 5,
                            "requires_strategy": True,
                        },
                    ),
                    OpenApiExample(
                        name="Empty category",
                        value={
                            "category_id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
                            "category_name": "Unused Category",
                            "course_count": 0,
                            "courses_by_status": {},
                            "affected_creator_profile_count": 0,
                            "requires_strategy": False,
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
    @action(
        detail=True,
        methods=["get"],
        url_path="deletion-impact",
        permission_classes=[CanManageCategories],
    )
    def deletion_impact(self, request, pk=None):
        impact = category_service.get_deletion_impact(category=self.get_object())
        return Response(CategoryDeletionImpactSerializer(impact).data, status=200)

    def destroy(self, request, *args, **kwargs):
        # Overridden rather than using perform_destroy: the strategy arrives as
        # query parameters (a DELETE body is stripped by some proxies) and needs
        # validating before anything is touched.
        params = CategoryDeletionSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)

        category_service.delete_category(
            category=self.get_object(),
            actor=request.user,
            strategy=params.validated_data.get("strategy"),
            replacement_category=params.validated_data.get("replacement_category"),
        )
        return Response(status=204)
