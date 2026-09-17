"""Swagger documentation for Roles & Permissions management."""

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse

from api.authorization.serializers.role_admin_serializer import (
    ChangeStaffRoleSerializer,
    RoleCreateSerializer,
    RoleUpdateSerializer,
)
from includes.spectacular.responses import (
    STANDARD_ERROR_RESPONSES,
    ErrorEnvelopeSerializer,
    inline_success_response,
)

TAG = "Admin — Roles & Permissions"

_VIEW_AUTH = (
    "**Auth:** The `roles.view` permission — Admin and Super Admin by default.\n\n"
)
_MANAGE_AUTH = (
    "**Auth:** The `roles.manage` permission — Super Admin by default — and a "
    "session verified with multi-factor authentication where MFA is enforced. "
    "Holders other than the Super Admin can only add or remove permissions "
    "they hold themselves, cannot change the role they hold, and cannot touch "
    "the Super Admin role.\n\n"
)

_ROLE_EXAMPLE = {
    "id": "0b6f8a9e-7c1d-4a2b-9e3f-5d6c7b8a9e0f",
    "name": "Content Lead",
    "description": "Writers who also curate categories.",
    "base_role": "STAFF_WRITER",
    "base_role_label": "Writer",
    "is_system": False,
    "is_locked": False,
    "is_deletable": True,
    "can_edit": True,
    "member_count": 3,
    "permissions": [
        "catalog.manage_categories",
        "courses.create",
        "earnings.manage_own",
    ],
}


def _envelope(data, message="Retrieved successfully", status=200):
    return {"success": True, "status": status, "message": message, "data": data}


def _conflict(description, code, message):
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
                                "code": code,
                                "message": message,
                                "field_name": None,
                            }
                        ]
                    },
                )
            ],
        )
    }


ROLE_LIST_DOCS = {
    "operation_id": "admin_roles_list",
    "summary": "List roles",
    "description": (
        "Returns every live role as a card: built-in roles first, in the "
        "platform's order, then custom roles by name. Each carries its "
        "permissions, active member count, and whether the caller may edit "
        "it.\n\n"
        "Called when the Roles & Permissions tab loads.\n\n"
        + _VIEW_AUTH
        + "**Prerequisites:** None.\n\n"
        "**Important:** Not paginated. Pair with `GET /admin/permissions/` "
        "for chip labels and groups. `permissions` holds codenames only."
    ),
    "tags": [TAG],
    "responses": {
        200: inline_success_response(
            description="Role cards.",
            examples=[OpenApiExample(name="Success", value=_envelope([_ROLE_EXAMPLE]))],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

ROLE_CREATE_DOCS = {
    "summary": "Create a role",
    "description": (
        "Creates a custom role from the Add new role dialog.\n\n"
        + _MANAGE_AUTH
        + "**Prerequisites:** `base_role` must be a staff role. The name must "
        "not match a live role, ignoring case.\n\n"
        "**Important:** `base_role` decides workflow behaviour the permissions "
        "don't: which review seats members sit, whether MFA is mandatory, and "
        "their workspace. It can't be changed later. Unknown codenames are "
        "400; codenames you don't hold are 403."
    ),
    "tags": [TAG],
    "request": RoleCreateSerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request",
            request_only=True,
            value={
                "name": "Content Lead",
                "description": "Writers who also curate categories.",
                "base_role": "STAFF_WRITER",
                "permissions": ["courses.create", "catalog.manage_categories"],
            },
        )
    ],
    "responses": {
        201: inline_success_response(
            description="The new role.",
            examples=[
                OpenApiExample(
                    name="Created", value=_envelope(_ROLE_EXAMPLE, "Role created.", 201)
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["validation"],
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **_conflict(
            "A live role already uses this name.",
            "role_conflict",
            "A role named 'Content Lead' already exists.",
        ),
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

ROLE_RETRIEVE_DOCS = {
    "summary": "Retrieve a role",
    "description": (
        "Returns one live role card.\n\n"
        + _VIEW_AUTH
        + "**Prerequisites:** The role must exist.\n\n"
        "**Important:** A deleted role is 404."
    ),
    "tags": [TAG],
    "responses": {
        200: inline_success_response(
            description="The role.",
            examples=[OpenApiExample(name="Success", value=_envelope(_ROLE_EXAMPLE))],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

ROLE_UPDATE_DOCS = {
    "summary": "Update a role",
    "description": (
        "Renames a custom role, or replaces a role's permissions with the set "
        "sent. Called when chips are toggled and saved.\n\n"
        + _MANAGE_AUTH
        + "**Prerequisites:** The role must exist and not be the Super Admin "
        "role. At least one field must be sent.\n\n"
        "**Important:** `permissions` is the complete new set, not a patch. "
        "Changes apply to every member on their next request, without signing "
        "them out. Built-in roles accept `permissions` only. Course Creator and "
        "Creator Reviewer cannot be given permissions that act on other "
        "people's accounts, money or settings (400), because every public "
        "sign-up would hold them."
    ),
    "tags": [TAG],
    "request": RoleUpdateSerializer,
    "examples": [
        OpenApiExample(
            name="Toggle chips",
            request_only=True,
            value={"permissions": ["courses.create", "earnings.manage_own"]},
        )
    ],
    "responses": {
        200: inline_success_response(
            description="The updated role.",
            examples=[
                OpenApiExample(
                    name="Success", value=_envelope(_ROLE_EXAMPLE, "Role updated.")
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["validation"],
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **_conflict(
            "A live role already uses the new name.",
            "role_conflict",
            "A role named 'Content Lead' already exists.",
        ),
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

ROLE_DELETE_DOCS = {
    "summary": "Delete a role",
    "description": (
        "Deletes a custom role. Members, if any, move to the role named by "
        "`reassign_to_role_id` and are signed out.\n\n"
        + _MANAGE_AUTH
        + "**Prerequisites:** The role must be a live custom role. With "
        "members, `reassign_to_role_id` must name another live role with the "
        "same base role, whose permissions you hold.\n\n"
        "**Important:** Built-in roles cannot be deleted (403). A role with "
        "members and no `reassign_to_role_id` returns 409 and nothing changes; "
        "use `member_count` from the role card to decide whether to ask."
    ),
    "tags": [TAG],
    "parameters": [
        OpenApiParameter(
            "reassign_to_role_id",
            str,
            required=False,
            description="Role to move members to. Required when the role has members.",
        )
    ],
    "responses": {
        200: inline_success_response(
            description="What the deletion did.",
            examples=[
                OpenApiExample(
                    name="Members moved",
                    value=_envelope(
                        {
                            "role_id": _ROLE_EXAMPLE["id"],
                            "members_moved": 3,
                            "moved_to_role": {
                                "id": "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
                                "name": "Writer",
                            },
                        },
                        "Role deleted.",
                    ),
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["validation"],
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **_conflict(
            "The role has members and no reassign_to_role_id was given.",
            "role_has_members",
            "'Content Lead' still has 3 member(s). Choose a role to move them to.",
        ),
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

ROLE_MEMBERS_DOCS = {
    "summary": "List a role's members",
    "description": (
        "Returns the users assigned to a role, newest first.\n\n"
        "**Auth:** The `roles.view` and `staff.view` permissions — Super Admin "
        "by default.\n\n"
        "**Prerequisites:** The role must exist.\n\n"
        "**Important:** Paginated under `data.results` / `data.paginator`; "
        "`?search=` matches email and name."
    ),
    "tags": [TAG],
    "parameters": [
        OpenApiParameter(
            "search", str, required=False, description="Match email or name."
        ),
        OpenApiParameter("page", int, required=False, description="Page number."),
        OpenApiParameter("size", int, required=False, description="Rows per page."),
    ],
    "responses": {
        200: inline_success_response(
            description="A page of members.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value={
                        "status": True,
                        "message": "Successfully retrieved data",
                        "data": {
                            "paginator": {
                                "count": 1,
                                "page": 1,
                                "page_size": 10,
                                "total_pages": 1,
                                "next": None,
                                "next_page_number": None,
                                "previous": None,
                                "previous_page_number": None,
                            },
                            "results": [
                                {
                                    "id": "9f8e7d6c-5b4a-4321-8765-0fedcba98765",
                                    "email": "ada@example.com",
                                    "full_name": "Ada Obi",
                                    "status": "ACTIVE",
                                    "is_active": True,
                                }
                            ],
                        },
                    },
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["not_found"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
}

PERMISSION_CATALOGUE_DOCS = {
    "summary": "List permission groups",
    "description": (
        "Returns every permission, grouped as the screen shows them: the "
        "design's chip groups first, then extra groups for capabilities the "
        "design doesn't show.\n\n"
        "Called when the Roles & Permissions tab loads, to label chips and to "
        "know which the caller may toggle (`grantable_by_you`).\n\n"
        + _VIEW_AUTH
        + "**Prerequisites:** None.\n\n"
        "**Important:** Codenames are stable; labels may change."
    ),
    "tags": [TAG],
    "responses": {
        200: inline_success_response(
            description="Permission groups.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value=_envelope(
                        [
                            {
                                "key": "courses",
                                "label": "Courses",
                                "is_design_group": True,
                                "permissions": [
                                    {
                                        "codename": "courses.approve",
                                        "label": "Approve Course",
                                        "description": "Claim and approve review seats your role is eligible for.",
                                        "implies": [],
                                        "grantable_to_public_roles": True,
                                        "grantable_by_you": True,
                                    }
                                ],
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

CHANGE_STAFF_ROLE_DOCS = {
    "summary": "Change a staff member's role",
    "description": (
        "Moves a staff member to another staff role, built-in or custom.\n\n"
        "**Auth:** The `staff.full_access` permission — Super Admin by default "
        "— and a session verified with multi-factor authentication where MFA "
        "is enforced. Holders other than the Super Admin can only assign roles "
        "whose permissions they hold, to people whose permissions they hold.\n\n"
        "**Prerequisites:** The target must be a staff member other than the "
        "caller and the Super Admin; the role must be a live staff role they "
        "don't already hold.\n\n"
        "**Important:** The member is signed out everywhere and notified. Any "
        "review seat they had claimed but not decided, which their new role "
        "cannot sit, is released back to the queue. Moving to an Admin-based "
        "role starts their MFA enrolment grace period."
    ),
    "tags": ["Admin — Teams"],
    "request": ChangeStaffRoleSerializer,
    "examples": [
        OpenApiExample(
            name="Sample Request",
            request_only=True,
            value={"role_id": _ROLE_EXAMPLE["id"]},
        )
    ],
    "responses": {
        200: inline_success_response(
            description="The staff member's new role.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value=_envelope(
                        {
                            "id": "9f8e7d6c-5b4a-4321-8765-0fedcba98765",
                            "role": "STAFF_WRITER",
                            "access_role": {
                                "id": _ROLE_EXAMPLE["id"],
                                "name": "Content Lead",
                            },
                        },
                        "Role changed.",
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
