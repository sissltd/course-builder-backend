from rest_framework import status
from rest_framework.exceptions import APIException


class RoleConflict(APIException):
    """A role name already used by a live role."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "role_conflict"
    default_detail = "A role with this name already exists."


class RoleHasMembers(APIException):
    """Deleting a role that still has members, without saying where they go.

    409 rather than 400: the request is well-formed but conflicts with the
    role's current state; the caller resolves it by choosing a role to move
    the members to (`reassign_to_role_id`).
    """

    status_code = status.HTTP_409_CONFLICT
    default_code = "role_has_members"
    default_detail = "This role still has members. Choose a role to move them to."
