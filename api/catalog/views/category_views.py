from django.db.models import Count
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

from api.catalog.enums import CategoryDeletionStrategy, CategoryStatus
from api.catalog.filters import CategoryFilter
from api.catalog.models import Category
from api.catalog.serializers import (
    CategoryStatsSerializer,
    CategoryDeletionImpactSerializer,
    CategoryDeletionSerializer,
    CategoryPickerSerializer,
    CategorySerializer,
    CategoryWriteSerializer,
)
from api.catalog.services import category_service
from api.authorization import codenames
from api.authorization.permissions import Perm
from api.users.permissions import IsMFAVerifiedForSession
from includes.spectacular.responses import (
    STANDARD_ERROR_RESPONSES,
    ErrorEnvelopeSerializer,
)

WRITE_ACTIONS = {"create", "update", "partial_update", "destroy"}
# Read endpoints that back the admin Categories screen rather than the
# creator picker - full category data, including per-tier pricing, is an
# Admin Writer concern, so the catalog stays a single source of truth.
ADMIN_READ_ACTIONS = {"list", "retrieve", "stats", "deletion_impact"}

_CATEGORY_EXAMPLE = {
    "id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
    "name": "Software Engineering",
    "creator_price_beginner": "150.00",
    "creator_price_intermediate": "200.00",
    "creator_price_advanced": "300.00",
    "icon": "code",
    "track_preference": "OPEN",
    "status": "ACTIVE",
    "total_courses": 12,
    "created_datetime": "2026-07-12T09:30:11.204Z",
    "updated_datetime": "2026-07-19T06:04:45.882Z",
}

_AUTH_LINE = (
    "**Auth:** The `catalog.manage_categories` permission — Writer, Admin and Super Admin by default. Approvers, "
    "Reviewers, and public Course Creators do not manage categories; they "
    "browse a lightweight picker via `GET /api/v1/categories/picker/`."
)

_PICKER_EXAMPLE = {
    "id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
    "name": "Software Engineering",
    "is_active": True,
}

_PRICE_WARNING = (
    "Editing the `creator_price_beginner`, `creator_price_intermediate`, or "
    "`creator_price_advanced` rates is not retroactive: "
    "`Course.creator_price_snapshot` freezes the rate when a course is "
    "submitted, so a change here only affects courses submitted afterwards "
    "and never alters an existing payout."
)


@extend_schema_view(
    list=extend_schema(
        summary="List course categories",
        description=(
            "Returns every course category with its per-difficulty creator "
            "prices, track preference, and whether it is accepting "
            "submissions. This is the table behind the admin Categories "
            "screen, where writers keep pricing and availability in order.\n\n"
            "Called when the admin Categories screen loads.\n\n"
            "**Auth:** The `catalog.manage_categories` permission — Writer, Admin and Super Admin by default. "
            "Approvers, Reviewers, and Course Creators cannot list the full "
            "catalog.\n\n"
            "**Prerequisites:** None beyond holding the Writer, Admin, or "
            "Super Admin role.\n\n"
            "**Important:** Includes `INACTIVE` and `ARCHIVED` categories. "
            "Results are paginated. Course creators use "
            "`GET /api/v1/categories/picker/` instead \u2014 a filtered view "
            "that only serves ACTIVE categories."
        ),
        tags=["Admin Writer — Category"],
        responses={
            200: OpenApiResponse(
                response=CategorySerializer(many=True),
                description="Categories, ordered by name.",
                examples=[OpenApiExample(name="Success", value=[_CATEGORY_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a course category",
        description=(
            "Returns a single category by id, including its per-difficulty "
            "creator prices and submission status. Backs the category "
            "detail/edit screen.\n\n"
            "Called when an admin opens a category from the Categories "
            "screen.\n\n"
            "**Auth:** The `catalog.manage_categories` permission — Writer, Admin and Super Admin by default.\n\n"
            "**Prerequisites:** The category must exist.\n\n"
            "**Important:** None."
        ),
        tags=["Admin Writer — Category"],
        responses={
            200: OpenApiResponse(
                response=CategorySerializer,
                description="The requested category.",
                examples=[OpenApiExample(name="Success", value=_CATEGORY_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
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
        tags=["Admin Writer — Category"],
        request=CategoryWriteSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "name": "Software Engineering",
                    "creator_price_beginner": "150.00",
                    "creator_price_intermediate": "200.00",
                    "creator_price_advanced": "300.00",
                    "icon": "code",
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
                response=ErrorEnvelopeSerializer,
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
                                    "field_name": "creator_price_beginner",
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
        tags=["Admin Writer — Category"],
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
        tags=["Admin Writer — Category"],
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
                value={"creator_price_advanced": "175.00"},
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
        tags=["Admin Writer — Category"],
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
            204: OpenApiResponse(description="Category soft-deleted."),
            400: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
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
                response=ErrorEnvelopeSerializer,
                description=(
                    "The category has courses and no strategy was given. "
                    "Nothing was changed."
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
    """Admin Writer-managed course categories (SCCS PRD Section 7).

    Every endpoint under /categories/ needs `catalog.manage_categories`
    (Writer, Admin and Super Admin by default). Create/update/delete
    additionally require an MFA-verified session (IsMFAVerifiedForSession),
    since categories carry creator pricing, a financial policy change.
    Approvers, reviewers, and public Course Creators are deliberately
    excluded; creators pick a category via GET /categories/picker/, which
    serves only ACTIVE categories in a minimal payload (US-101).
    """

    queryset = Category.objects.all()
    filterset_class = CategoryFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = [
        "name",
        "creator_price_beginner",
        "creator_price_advanced",
        "created_datetime",
    ]

    def get_queryset(self):
        # Annotated once here so listing N categories costs one query
        # rather than a count per row.
        # Explicit order_by: the annotation's GROUP BY leaves the queryset
        # unordered as far as the paginator is concerned, which makes page
        # boundaries non-deterministic.
        return (
            super()
            .get_queryset()
            .filter(is_deleted=False)
            .annotate(total_courses=Count("courses"))
            .order_by("name")
        )

    def get_serializer_class(self):
        if self.action in WRITE_ACTIONS:
            return CategoryWriteSerializer
        if self.action == "deletion_impact":
            return CategoryDeletionImpactSerializer
        if self.action == "picker":
            return CategoryPickerSerializer
        return CategorySerializer

    def get_permissions(self):
        if self.action in WRITE_ACTIONS or self.action in ("archive", "unarchive"):
            return [Perm(codenames.CATALOG_MANAGE_CATEGORIES)(), IsMFAVerifiedForSession()]
        if self.action in ADMIN_READ_ACTIONS:
            return [Perm(codenames.CATALOG_MANAGE_CATEGORIES)()]
        return super().get_permissions()

    @extend_schema(
        summary="Category counts by status",
        description=(
            "Returns the total, active, inactive and archived category "
            "counts behind the tiles above the admin Categories table.\n\n"
            "Called when the admin Categories screen loads.\n\n"
            "**Auth:** The `catalog.manage_categories` permission — Writer, Admin and Super Admin by default.\n\n"
            "**Prerequisites:** None beyond holding the Writer, Admin, or "
            "Super Admin role.\n\n"
            "**Important:** Every key is always present, including zeroes, "
            "so a tile never disappears when its bucket empties."
        ),
        tags=["Admin Writer — Category"],
        responses={
            200: OpenApiResponse(
                response=CategoryStatsSerializer,
                description="Counts by status.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "total": 24,
                            "active": 18,
                            "inactive": 4,
                            "archived": 2,
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=False, methods=["get"])
    def stats(self, request):
        return Response(
            CategoryStatsSerializer(category_service.get_category_stats()).data
        )

    @extend_schema(
        summary="List pickable categories",
        description=(
            "Returns the categories a creator can file a new course under: "
            "a lightweight picker payload with the name and whether the "
            "category accepts submissions. Only ACTIVE "
            "categories are served \u2014 INACTIVE and ARCHIVED are never "
            "offered for creation.\n\n"
            "Called when the category picker opens during course creation.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Every returned row is ACTIVE, so `is_active` is "
            "always true \u2014 the flag mirrors the on/off state the admin "
            "toggles rather than signalling a mix. Use the returned `id` "
            "when creating a course."
        ),
        tags=["Creator — Categories"],
        responses={
            200: OpenApiResponse(
                response=CategoryPickerSerializer(many=True),
                description="Active categories, ordered by name.",
                examples=[
                    OpenApiExample(name="Success", value=[_PICKER_EXAMPLE])
                ],
            ),
**STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
)
    @action(detail=False, methods=["get"], pagination_class=None)
    def picker(self, request):
        categories = Category.objects.filter(
            status=CategoryStatus.ACTIVE, is_deleted=False
        ).order_by("name")
        return Response(CategoryPickerSerializer(categories, many=True).data)

    @extend_schema(
        summary="Archive a category",
        description=(
            "Retires a category so creators can no longer view it or file "
            "new courses under it, without deleting anything.\n\n"
            "Use this instead of delete when a category is being retired "
            "but its courses must stay. Reversible via unarchive.\n\n"
            "**Auth:** The `catalog.manage_categories` permission — Writer, Admin and Super Admin by default, with an MFA-verified session "
            "where MFA is mandatory for the caller's role.\n\n"
            "**Prerequisites:** The category must not already be archived.\n\n"
            "**Important:** Existing courses, payouts and price snapshots "
            "are untouched \u2014 archiving only removes the category from "
            "the creator picker. Archiving an already-archived category is "
            "a 400, not a silent success."
        ),
        tags=["Admin Writer — Category"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=CategorySerializer,
                description="The archived category.",
                examples=[OpenApiExample(name="Success", value=_CATEGORY_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        category = category_service.archive_category(
            category=self.get_object(), actor=request.user
        )
        return Response(CategorySerializer(category).data)

    @extend_schema(
        summary="Restore an archived category",
        description=(
            "Returns an archived category to ACTIVE so creators can use it "
            "again.\n\n"
            "**Auth:** The `catalog.manage_categories` permission — Writer, Admin and Super Admin by default, with an MFA-verified session "
            "where MFA is mandatory for the caller's role.\n\n"
            "**Prerequisites:** The category must currently be archived.\n\n"
            "**Important:** Restores to ACTIVE, not to whatever status it "
            "held before archiving \u2014 an INACTIVE category that was "
            "archived comes back active."
        ),
        tags=["Admin Writer — Category"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=CategorySerializer,
                description="The restored category.",
                examples=[OpenApiExample(name="Success", value=_CATEGORY_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def unarchive(self, request, pk=None):
        category = category_service.unarchive_category(
            category=self.get_object(), actor=request.user
        )
        return Response(CategorySerializer(category).data)

    @extend_schema(
        summary="Preview what deleting a category would affect",
        description=(
            "Reports how many courses live in a category, broken down by "
            "status, plus how many onboarding profiles name it as their "
            "primary expertise. Nothing is changed — this exists so the delete "
            "confirmation can warn the admin with real numbers and offer the "
            "right choice before the category is soft-deleted.\n\n"
            "Called when the admin clicks delete on the Categories screen, "
            "before `DELETE /categories/{id}/` is sent with their decision.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The category must exist.\n\n"
            "**Important:** `requires_strategy` tells you whether the delete "
            "dialog needs to ask anything at all — when it is false, a plain "
            "DELETE soft-deletes the category. Treat the counts as a snapshot: a course created "
            "between this call and the delete is included in whatever strategy "
            "runs. `affected_creator_profile_count` is informational — those "
            "profiles are never deleted and retain their reference because the "
            "category row is soft-deleted."
        ),
        tags=["Admin Writer — Category"],
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
        permission_classes=[Perm(codenames.CATALOG_MANAGE_CATEGORIES)],
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
