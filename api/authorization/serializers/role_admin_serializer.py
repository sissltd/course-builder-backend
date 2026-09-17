from rest_framework import serializers

from api.authorization import policy
from api.authorization.services import role_admin_service
from api.users.enums import INVITABLE_STAFF_ROLE_CHOICES, UserRole


class RoleSerializer(serializers.Serializer):
    """A role card on the Roles & Permissions screen.

    Needs `actor` in context to compute `can_edit`.
    """

    id = serializers.UUIDField(help_text="Role id.")
    name = serializers.CharField(help_text="Role name.")
    description = serializers.CharField(help_text="What the role is for; may be empty.")
    base_role = serializers.ChoiceField(
        choices=UserRole.choices,
        help_text="The built-in role whose workflow members follow (review seats, MFA, workspace).",
    )
    base_role_label = serializers.SerializerMethodField(
        help_text="Display name for base_role."
    )
    is_system = serializers.SerializerMethodField(help_text="True for built-in roles.")
    is_locked = serializers.SerializerMethodField(
        help_text="True for the Super Admin role, which always holds every permission."
    )
    is_deletable = serializers.SerializerMethodField(
        help_text="False for built-in roles."
    )
    can_edit = serializers.SerializerMethodField(
        help_text=(
            "Whether the caller may change this role. You still can only add or "
            "remove permissions you hold (see grantable_by_you on the catalogue)."
        )
    )
    member_count = serializers.IntegerField(help_text="Active users holding the role.")
    permissions = serializers.SerializerMethodField(
        help_text="Permission codenames the role holds, sorted."
    )

    def get_base_role_label(self, obj) -> str:
        return UserRole(obj.base_role).label

    def get_is_system(self, obj) -> bool:
        return policy.is_system(obj)

    def get_is_locked(self, obj) -> bool:
        return policy.is_locked(obj)

    def get_is_deletable(self, obj) -> bool:
        return policy.is_deletable(obj)

    def get_can_edit(self, obj) -> bool:
        return role_admin_service.can_edit(actor=self.context["actor"], role=obj)

    def get_permissions(self, obj) -> list[str]:
        return role_admin_service.granted_codenames(obj)


class RoleCreateSerializer(serializers.Serializer):
    name = serializers.CharField(
        max_length=60, help_text="Role name, unique among live roles."
    )
    description = serializers.CharField(
        required=False, allow_blank=True, default="", help_text="Optional note."
    )
    base_role = serializers.ChoiceField(
        choices=INVITABLE_STAFF_ROLE_CHOICES,
        help_text="The staff role whose workflow members follow. Fixed once created.",
    )
    permissions = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=True,
        help_text="Permission codenames to grant. You may only grant ones you hold.",
    )


class RoleUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(
        max_length=60, required=False, help_text="New name. Custom roles only."
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="New description. Custom roles only.",
    )
    permissions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        help_text=(
            "The complete new permission set, replacing the current one. You may "
            "only add or remove permissions you hold."
        ),
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide at least one field to update.")
        return attrs


class RoleDeleteQuerySerializer(serializers.Serializer):
    reassign_to_role_id = serializers.UUIDField(
        required=False,
        help_text="Where the role's members move. Required when the role has members.",
    )


class RoleReferenceSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="Role id.")
    name = serializers.CharField(help_text="Role name.")


class RoleDeletionResultSerializer(serializers.Serializer):
    role_id = serializers.UUIDField(help_text="The deleted role.")
    members_moved = serializers.IntegerField(help_text="Members moved to another role.")
    moved_to_role = RoleReferenceSerializer(
        allow_null=True, help_text="The role members moved to, or null if it had none."
    )


class RoleMemberSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="User id.")
    email = serializers.EmailField(help_text="Email.")
    full_name = serializers.CharField(source="get_full_name", help_text="Display name.")
    status = serializers.CharField(help_text="Account status.")
    is_active = serializers.BooleanField(help_text="Whether the account can sign in.")


class CatalogPermissionSerializer(serializers.Serializer):
    codename = serializers.CharField(help_text="Stable permission codename.")
    label = serializers.CharField(help_text="Chip text.")
    description = serializers.CharField(help_text="What the permission allows.")
    implies = serializers.ListField(
        child=serializers.CharField(),
        help_text="Codenames granted along with this one.",
    )
    grantable_to_public_roles = serializers.BooleanField(
        help_text="Whether it may be granted to Course Creator or Creator Reviewer."
    )
    grantable_by_you = serializers.BooleanField(
        help_text="Whether the caller may add or remove this permission on a role."
    )


class PermissionGroupSerializer(serializers.Serializer):
    key = serializers.CharField(help_text="Stable group key.")
    label = serializers.CharField(help_text="Group heading.")
    is_design_group = serializers.BooleanField(
        help_text="True for the design's chip groups; false for the extra capability groups."
    )
    permissions = CatalogPermissionSerializer(
        many=True, help_text="Permissions in the group."
    )


class ChangeStaffRoleSerializer(serializers.Serializer):
    role_id = serializers.UUIDField(help_text="The staff role to move the member to.")
