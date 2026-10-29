"""Swagger documentation for the achievements endpoints."""

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse

from api.achievements.serializers.admin_badge_serializer import (
    BadgeAwardCreateSerializer,
    BadgeCreateSerializer,
    BadgeUpdateSerializer,
)
from includes.spectacular.responses import (
    STANDARD_ERROR_RESPONSES,
    ErrorEnvelopeSerializer,
    inline_success_response,
)

ADMIN_TAG = "Admin — Achievements"
CREATOR_TAG = "Creator — Achievements"

_MANAGER_AUTH = (
    "**Auth:** The `achievements.manage` permission — Writer, Admin and "
    "Super Admin by default.\n\n"
)

_BADGE_EXAMPLE = {
    "id": "5b0c7f1e-8a61-4c2e-9d4b-3f2a1e6c7d90",
    "title": "Top",
    "icon": "diamond",
    "color": "#F2994A",
    "criterion": "COURSES_CREATED",
    "criterion_label": "Courses created",
    "required_count": 100,
    "auto_award": True,
    "requirement_summary": "For creators who have created 100 courses",
    "holder_count": 203,
    "created_datetime": "2026-09-16T10:00:00Z",
    "updated_datetime": "2026-09-16T10:00:00Z",
}

_RUNG_EXAMPLE = {
    "id": "a1d9e3c4-2b7f-4e10-8c5a-6f9b0d2e1c33",
    "title": "Professional",
    "icon": "medal",
    "color": "#7B61FF",
    "required_count": 50,
}

_HOLDER_EXAMPLE = {
    "id": "c3e4f5a6-1b2c-4d3e-8f9a-0b1c2d3e4f5a",
    "creator": {
        "id": "9f8e7d6c-5b4a-4321-8765-0fedcba98765",
        "email": "osaite@example.com",
        "full_name": "Osaite Emmanuel",
        "avatar_url": "profiles/osaite.png",
    },
    "source": "AUTOMATIC",
    "awarded_by_email": None,
    "awarded_at": "2026-09-16T10:00:00Z",
}


def _envelope(data, message="Retrieved successfully", status=200):
    return {"success": True, "status": status, "message": message, "data": data}


def _paginated(results):
    return {
        "status": True,
        "message": "Successfully retrieved data",
        "data": {
            "paginator": {
                "count": len(results),
                "page": 1,
                "page_size": 10,
                "total_pages": 1,
                "next": None,
                "next_page_number": None,
                "previous": None,
                "previous_page_number": None,
            },
            "results": results,
        },
    }


def _conflict(description, message):
    return {
        409: OpenApiResponse(
            response=ErrorEnvelopeSerializer,
            description=description,
            examples=[
                OpenApiExample(
                    name="Conflict",
                    value={
                        "errors": [
                            {
                                "type": "client_error",
                                "code": "badge_conflict",
                                "message": message,
                                "field_name": None,
                            }
                        ]
                    },
                )
            ],
        )
    }


BADGE_LIST_DOCS = {
    "operation_id": "admin_achievements_badges_list",
    "summary": "List badges",
    "description": (
        "Returns every live badge, highest requirement first, each with how "
        "many creators currently hold it.\n\n"
        "Called when the Achievement award screen loads. The same rows feed "
        "the Achievement Analytics cards through `holder_count`, so there is "
        "no separate analytics call.\n\n"
        + _MANAGER_AUTH
        + "**Prerequisites:** None.\n\n"
        "**Important:** Paginated: rows sit under `data.results` with "
        "`data.paginator` beside them. Use `size` to set the page size. "
        "Deleted badges never appear."
    ),
    "tags": [ADMIN_TAG],
    "parameters": [
        OpenApiParameter("page", int, required=False, description="Page number."),
        OpenApiParameter("size", int, required=False, description="Rows per page."),
    ],
    "responses": {
        200: inline_success_response(
            description="A page of badges.",
            examples=[
                OpenApiExample(name="Success", value=_paginated([_BADGE_EXAMPLE]))
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BADGE_CREATE_DOCS = {
    "summary": "Create a badge",
    "description": (
        "Creates a badge from the Add new badge dialog.\n\n"
        "Called when the dialog's Add badge button is pressed.\n\n"
        + _MANAGER_AUTH
        + "**Prerequisites:** The title must not match a live badge's title, "
        "ignoring case, and no live badge may already use the same "
        "`criterion` and `required_count`.\n\n"
        "**Important:** With `auto_award` on, every creator who already meets "
        "the requirement is awarded the badge and notified in the same "
        "request, so `holder_count` in the response can already be above "
        "zero. `criterion` cannot be changed later. The dialog has no "
        "criterion picker yet, so omitting it counts courses created."
    ),
    "tags": [ADMIN_TAG],
    "request": BadgeCreateSerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request",
            request_only=True,
            value={
                "title": "Top",
                "icon": "diamond",
                "color": "#F2994A",
                "criterion": "COURSES_CREATED",
                "required_count": 100,
                "auto_award": True,
            },
        )
    ],
    "responses": {
        201: inline_success_response(
            description="The new badge.",
            examples=[
                OpenApiExample(
                    name="Created",
                    value=_envelope(_BADGE_EXAMPLE, "Badge created.", 201),
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["validation"],
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **_conflict(
            "A live badge already has this title, or this criterion and count.",
            "A badge named 'Top' already exists.",
        ),
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BADGE_RETRIEVE_DOCS = {
    "summary": "Retrieve a badge",
    "description": (
        "Returns one live badge, e.g. to prefill the Edit or Configure dialog.\n\n"
        + _MANAGER_AUTH
        + "**Prerequisites:** The badge must exist and not be deleted.\n\n"
        "**Important:** A deleted badge returns 404, the same as an unknown id."
    ),
    "tags": [ADMIN_TAG],
    "responses": {
        200: inline_success_response(
            description="The badge.",
            examples=[OpenApiExample(name="Success", value=_envelope(_BADGE_EXAMPLE))],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BADGE_UPDATE_DOCS = {
    "summary": "Update a badge",
    "description": (
        "Saves the Edit dialog (title, icon, colour) or the Configure dialog "
        "(`required_count`). Every field is optional; send only what "
        "changed.\n\n"
        + _MANAGER_AUTH
        + "**Prerequisites:** The badge must exist. At least one field must be "
        "sent.\n\n"
        "**Important:** Raising `required_count` never takes the badge away "
        "from creators who already hold it. Lowering it, or switching "
        "`auto_award` on, awards the badge in the same request to every "
        "creator who now qualifies. `criterion` cannot be changed; create a "
        "new badge instead."
    ),
    "tags": [ADMIN_TAG],
    "request": BadgeUpdateSerializer,
    "examples": [
        OpenApiExample(
            name="Configure",
            request_only=True,
            value={"required_count": 23},
        ),
        OpenApiExample(
            name="Edit",
            request_only=True,
            value={"title": "Top Creator", "icon": "diamond", "color": "#F2C94C"},
        ),
    ],
    "responses": {
        200: inline_success_response(
            description="The updated badge.",
            examples=[
                OpenApiExample(
                    name="Success", value=_envelope(_BADGE_EXAMPLE, "Badge updated.")
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["validation"],
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **_conflict(
            "The new title or requirement collides with another live badge.",
            "Another badge already requires this many courses for the same criterion.",
        ),
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BADGE_DELETE_DOCS = {
    "summary": "Delete a badge",
    "description": (
        "Deletes a badge. Every creator holding it loses it, and is notified.\n\n"
        "Called from the Delete this badge dialog, after "
        "`GET .../deletion-impact/` has said whether a previous badge exists.\n\n"
        + _MANAGER_AUTH
        + "**Prerequisites:** The badge must exist. `move_to_previous=true` "
        "needs a live badge with the same criterion and a lower "
        "`required_count`.\n\n"
        "**Important:** `move_to_previous=true` gives holders the badge one "
        "rung below before removing this one. Creators who already hold that "
        "badge keep their original award. If there is no lower badge the "
        "request is refused with 400 and nothing is deleted. The badge and its "
        "awards are soft-deleted, not destroyed, so the history remains."
    ),
    "tags": [ADMIN_TAG],
    "parameters": [
        OpenApiParameter(
            "move_to_previous",
            bool,
            required=False,
            description="Move holders to the previous badge on the ladder. Default false.",
        )
    ],
    "responses": {
        200: inline_success_response(
            description="What the deletion did.",
            examples=[
                OpenApiExample(
                    name="Moved to previous",
                    value=_envelope(
                        {
                            "badge_id": _BADGE_EXAMPLE["id"],
                            "holders_removed": 203,
                            "moved_to_badge": _RUNG_EXAMPLE,
                            "holders_moved": 180,
                        },
                        "Badge deleted.",
                    ),
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["validation"],
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BADGE_DELETION_IMPACT_DOCS = {
    "summary": "Preview deleting a badge",
    "description": (
        "Returns how many creators would lose the badge, and the badge they "
        "could move down to.\n\n"
        'Called when the Delete this badge dialog opens. Disable the "Move '
        'creators having this badge to the previous badge" toggle when '
        "`previous_badge` is null.\n\n"
        + _MANAGER_AUTH
        + "**Prerequisites:** The badge must exist.\n\n"
        "**Important:** Read-only; nothing changes."
    ),
    "tags": [ADMIN_TAG],
    "responses": {
        200: inline_success_response(
            description="The deletion impact.",
            examples=[
                OpenApiExample(
                    name="Has a previous badge",
                    value=_envelope(
                        {
                            "badge_id": _BADGE_EXAMPLE["id"],
                            "holder_count": 203,
                            "previous_badge": _RUNG_EXAMPLE,
                        }
                    ),
                ),
                OpenApiExample(
                    name="Lowest rung",
                    value=_envelope(
                        {
                            "badge_id": _BADGE_EXAMPLE["id"],
                            "holder_count": 12,
                            "previous_badge": None,
                        }
                    ),
                ),
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BADGE_HOLDER_LIST_DOCS = {
    "operation_id": "admin_achievements_badge_holders_list",
    "summary": "List a badge's holders",
    "description": (
        "Returns the creators currently holding a badge, newest award first.\n\n"
        "Called when staff drill into an Achievement Analytics card.\n\n"
        + _MANAGER_AUTH
        + "**Prerequisites:** The badge must exist.\n\n"
        "**Important:** Paginated under `data.results` / `data.paginator`; "
        "use `size` to set the page size."
    ),
    "tags": [ADMIN_TAG],
    "parameters": [
        OpenApiParameter("page", int, required=False, description="Page number."),
        OpenApiParameter("size", int, required=False, description="Rows per page."),
    ],
    "responses": {
        200: inline_success_response(
            description="A page of holders.",
            examples=[
                OpenApiExample(name="Success", value=_paginated([_HOLDER_EXAMPLE]))
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BADGE_AWARD_DOCS = {
    "summary": "Award a badge by hand",
    "description": (
        "Gives a badge to one creator whatever their course count. This is how "
        "a badge with `auto_award` off is ever held.\n\n"
        + _MANAGER_AUTH
        + "**Prerequisites:** The badge must exist, and `creator_id` must be a "
        "Course Creator or Writer.\n\n"
        "**Important:** Any other user id returns 404, the same as an unknown "
        "one. The creator is notified in-app."
    ),
    "tags": [ADMIN_TAG],
    "request": BadgeAwardCreateSerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request",
            request_only=True,
            value={"creator_id": _HOLDER_EXAMPLE["creator"]["id"]},
        )
    ],
    "responses": {
        201: inline_success_response(
            description="The new award.",
            examples=[
                OpenApiExample(
                    name="Created",
                    value=_envelope(
                        {
                            **_HOLDER_EXAMPLE,
                            "source": "MANUAL",
                            "awarded_by_email": "admin@example.com",
                        },
                        "Badge awarded.",
                        201,
                    ),
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["validation"],
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **_conflict(
            "The creator already holds this badge.",
            "This creator already holds this badge.",
        ),
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

BADGE_REVOKE_DOCS = {
    "summary": "Revoke a badge from a creator",
    "description": (
        "Takes a badge away from one creator.\n\n"
        + _MANAGER_AUTH
        + "**Prerequisites:** The creator must currently hold the badge.\n\n"
        "**Important:** If the badge auto-awards and the creator still "
        "qualifies, their next qualifying course event awards it again. "
        "Revoking is for correcting mistakes. The creator is not notified."
    ),
    "tags": [ADMIN_TAG],
    "responses": {
        200: inline_success_response(
            description="The badge was revoked.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value={"success": True, "status": 200, "message": "Badge revoked."},
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

CREATOR_ACHIEVEMENTS_DOCS = {
    "summary": "List my achievements",
    "description": (
        "Returns every live badge with the signed-in creator's progress: "
        "whether they hold it, when they got it, and their current count "
        "for its criterion.\n\n"
        "Called when the creator's achievements area loads.\n\n"
        "**Auth:** The `courses.create` permission — Course Creator and Writer "
        "by default.\n\n"
        "**Prerequisites:** None.\n\n"
        "**Important:** Not paginated; the badge list is small and "
        "staff-curated. Badges are awarded in the background shortly after a "
        "qualifying course event, not in the same request. Badges the creator "
        "holds also appear on `GET /users/me/` under `badges`."
    ),
    "tags": [CREATOR_TAG],
    "responses": {
        200: inline_success_response(
            description="Badges with progress.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value=_envelope(
                        [
                            {
                                "badge": {
                                    key: _BADGE_EXAMPLE[key]
                                    for key in (
                                        "id",
                                        "title",
                                        "icon",
                                        "color",
                                        "criterion",
                                        "criterion_label",
                                        "required_count",
                                        "requirement_summary",
                                    )
                                },
                                "earned": False,
                                "awarded_at": None,
                                "current_count": 37,
                            }
                        ]
                    ),
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}
