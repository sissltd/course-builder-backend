from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import (
    CollaboratorInvite,
    CourseCollaborator,
    WorkspaceCollaborator,
)
from api.catalog.serializers.category_serializer import CategoryMiniSerializer
from api.courses.models import Module
from api.courses.serializers.module_serializer import ModuleMiniSerializer


def user_display_name(user) -> str:
    """How every collaborator screen labels a person: their full name when
    set, their email otherwise."""

    return (
        " ".join(part for part in (user.first_name, user.last_name) if part).strip()
        or user.email
    )


def user_mini(user) -> dict | None:
    """A user as the collaborator screens embed one: id, display name, email.

    Shared by every "who is this person" field below, so a name is derived the
    same way whether it names a collaborator, an invitee or an inviter.
    """

    if user is None:
        return None
    return {"id": user.id, "name": user_display_name(user), "email": user.email}


class CollaboratorSerializer(serializers.ModelSerializer):
    """Collaborator fields named for the Collaborators screen.

    The screen renders a single row from ``name``, ``email``, ``date_added``
    and ``role``.  ``country_of_origin`` is used in its detail drawer.
    """

    name = serializers.SerializerMethodField()
    email = serializers.EmailField(source="user.email", read_only=True)
    country_of_origin = serializers.CharField(source="user.country", read_only=True)
    date_added = serializers.DateTimeField(source="created_datetime", read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    assigned_modules = ModuleMiniSerializer(many=True, read_only=True)
    course_id = serializers.UUIDField(source="course.id", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    category = CategoryMiniSerializer(source="course.category", read_only=True)
    source = serializers.SerializerMethodField()

    class Meta:
        model = CourseCollaborator
        fields = [
            "id",
            "name",
            "email",
            "country_of_origin",
            "date_added",
            "role",
            "role_label",
            "course_id",
            "course_title",
            "category",
            "assigned_modules",
            "source",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_name(self, obj) -> str:
        return user_display_name(obj.user)

    @extend_schema_field(serializers.ChoiceField(choices=["course", "workspace"]))
    def get_source(self, obj) -> str:
        """`course` rows support the /collaborators/{id}/ detail actions."""

        return "course"


class WorkspaceMemberCollaboratorSerializer(CollaboratorSerializer):
    """An ACTIVE workspace member with no course assignment yet, shaped like a
    CollaboratorSerializer row so the global Collaborators list can show
    them. The course fields are null and `id` is the workspace roster id -
    /collaborators/{id}/ detail actions don't apply to these rows."""

    country_of_origin = serializers.SerializerMethodField()
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    course_id = serializers.SerializerMethodField()
    course_title = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    assigned_modules = serializers.SerializerMethodField()

    class Meta(CollaboratorSerializer.Meta):
        model = WorkspaceCollaborator

    @extend_schema_field(serializers.CharField())
    def get_country_of_origin(self, obj) -> str:
        return obj.user.country or obj.country_of_origin

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_course_id(self, obj) -> None:
        return None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_course_title(self, obj) -> None:
        return None

    @extend_schema_field(CategoryMiniSerializer(allow_null=True))
    def get_category(self, obj) -> None:
        return None

    @extend_schema_field(ModuleMiniSerializer(many=True))
    def get_assigned_modules(self, obj) -> list:
        return []

    @extend_schema_field(serializers.ChoiceField(choices=["course", "workspace"]))
    def get_source(self, obj) -> str:
        return "workspace"


class CollaboratorInviteCreateSerializer(serializers.Serializer):
    """Request body for sending a course collaboration invite."""

    course_id = serializers.UUIDField()
    email = serializers.EmailField()
    role = serializers.ChoiceField(
        choices=CollaboratorRole.choices,
        required=False,
        default=CollaboratorRole.COLLABORATOR,
    )
    assigned_modules = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Module.objects.all(), required=False
    )


class CollaboratorInviteSerializer(serializers.ModelSerializer):
    """Representation of a CollaboratorInvite across its lifecycle.

    `invitee` is resolved from the email when an account exists, so the
    manage panel can show a name instead of just an address. The token is
    excluded from list output - it identifies the invite in notification
    deep-links and isn't part of the management surface.

    `course_title`, `role_label` and `invited_by` are what the invitee's inbox
    renders ("<inviter> invited you to collaborate on <course> as <role>").
    They are embedded rather than left to a follow-up fetch because an invitee
    has no access to the course until they accept - `GET /courses/{id}/` would
    404 for the one person this payload is for.
    """

    assigned_modules = ModuleMiniSerializer(many=True, read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    invitee = serializers.SerializerMethodField()
    invited_by = serializers.SerializerMethodField()
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = CollaboratorInvite
        fields = [
            "id",
            "course",
            "course_title",
            "email",
            "invitee",
            "invited_by",
            "role",
            "role_label",
            "assigned_modules",
            "status",
            "is_expired",
            "expires_at",
            "responded_at",
            "created_datetime",
        ]
        read_only_fields = fields

    def get_is_expired(self, obj) -> bool:
        from api.collaborators.services import invite_service

        return invite_service.invite_is_expired(obj)

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_invitee(self, obj) -> dict | None:
        """Resolve the invite's email to an account when one exists, flat
        style matching CollaboratorSerializer's screen fields. Null while the
        recipient has not signed up yet."""

        return user_mini(obj.invitee_user)

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_invited_by(self, obj) -> dict | None:
        """Who sent the invite, for the invitee's "<name> invited you" line.
        Null if that account has since been deleted."""

        return user_mini(obj.invited_by)


class CollaboratorRoleUpdateSerializer(serializers.Serializer):
    """Request body for changing a collaborator's role and/or module
    assignment. Both fields are optional so a PATCH can update either
    independently - DRF's `partial=True` on this view makes that so even
    though `role` isn't itself declared with `required=False`."""

    role = serializers.ChoiceField(choices=CollaboratorRole.choices, required=False)
    assigned_modules = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Module.objects.all(), required=False
    )
