from drf_spectacular.utils import extend_schema
from rest_framework import exceptions, status
from rest_framework.views import APIView

from api.authorization import codenames
from api.authorization.docs.role_admin_docs import (
    CHANGE_STAFF_ROLE_DOCS,
    PERMISSION_CATALOGUE_DOCS,
    ROLE_CREATE_DOCS,
    ROLE_DELETE_DOCS,
    ROLE_LIST_DOCS,
    ROLE_MEMBERS_DOCS,
    ROLE_RETRIEVE_DOCS,
    ROLE_UPDATE_DOCS,
)
from api.authorization.models import Role
from api.authorization.permissions import IsStrongMFASession, Perm
from api.authorization.serializers.role_admin_serializer import (
    ChangeStaffRoleSerializer,
    PermissionGroupSerializer,
    RoleCreateSerializer,
    RoleDeleteQuerySerializer,
    RoleDeletionResultSerializer,
    RoleMemberSerializer,
    RoleSerializer,
    RoleUpdateSerializer,
)
from api.authorization.services import role_admin_service
from api.users.models import User
from includes.helpers.pagination import PageNumberAPIPagination
from shared.response.success import custom_success_response

_MANAGE = [Perm(codenames.ROLES_MANAGE), IsStrongMFASession]


class RoleListCreateView(APIView):
    """Role cards, and the Add new role dialog."""

    serializer_class = RoleSerializer  # schema generation only

    def get_permissions(self):
        if self.request.method == "POST":
            return [permission() for permission in _MANAGE]
        return [Perm(codenames.ROLES_VIEW)()]

    @extend_schema(**ROLE_LIST_DOCS)
    def get(self, request):
        roles = role_admin_service.list_roles(actor=request.user)
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Retrieved successfully",
            data=RoleSerializer(roles, many=True, context={"actor": request.user}).data,
        )

    @extend_schema(**ROLE_CREATE_DOCS)
    def post(self, request):
        serializer = RoleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = role_admin_service.create_role(
            actor=request.user, request=request, **serializer.validated_data
        )
        return custom_success_response(
            status=status.HTTP_201_CREATED,
            message="Role created.",
            data=RoleSerializer(role, context={"actor": request.user}).data,
        )


class RoleDetailView(APIView):
    """One role: read, edit its chips or name, delete."""

    serializer_class = RoleSerializer  # schema generation only

    def get_permissions(self):
        if self.request.method in ("PATCH", "DELETE"):
            return [permission() for permission in _MANAGE]
        return [Perm(codenames.ROLES_VIEW)()]

    def _role(self, request, role_id):
        # Resolved without the ROLES_VIEW re-check get_role does: a manager's
        # gate is ROLES_MANAGE, which implies it anyway.
        role = (
            Role.objects.filter(id=role_id, is_deleted=False)
            .prefetch_related("grants")
            .first()
        )
        if role is None:
            raise exceptions.NotFound("Role not found.")
        return role

    @extend_schema(**ROLE_RETRIEVE_DOCS)
    def get(self, request, role_id):
        role = role_admin_service.get_role(actor=request.user, role_id=role_id)
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Retrieved successfully",
            data=RoleSerializer(role, context={"actor": request.user}).data,
        )

    @extend_schema(**ROLE_UPDATE_DOCS)
    def patch(self, request, role_id):
        role = self._role(request, role_id)
        serializer = RoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = role_admin_service.update_role(
            actor=request.user, role=role, request=request, **serializer.validated_data
        )
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Role updated.",
            data=RoleSerializer(role, context={"actor": request.user}).data,
        )

    @extend_schema(**ROLE_DELETE_DOCS)
    def delete(self, request, role_id):
        role = self._role(request, role_id)
        query = RoleDeleteQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        result = role_admin_service.delete_role(
            actor=request.user,
            role=role,
            reassign_to_role_id=query.validated_data.get("reassign_to_role_id"),
            request=request,
        )
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Role deleted.",
            data=RoleDeletionResultSerializer(result).data,
        )


class RoleMembersView(APIView):
    """Users assigned to a role."""

    permission_classes = [Perm(codenames.ROLES_VIEW), Perm(codenames.STAFF_VIEW)]
    pagination_class = PageNumberAPIPagination
    serializer_class = RoleMemberSerializer  # schema generation only

    @extend_schema(**ROLE_MEMBERS_DOCS)
    def get(self, request, role_id):
        role = role_admin_service.get_role(actor=request.user, role_id=role_id)
        members = role_admin_service.list_members(
            actor=request.user, role=role, search=request.query_params.get("search", "")
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(members, request, self)
        return paginator.get_paginated_response(
            RoleMemberSerializer(page, many=True).data
        )


class PermissionCatalogueView(APIView):
    """Permission groups and chips for the Roles & Permissions screen."""

    permission_classes = [Perm(codenames.ROLES_VIEW)]
    serializer_class = PermissionGroupSerializer  # schema generation only

    @extend_schema(**PERMISSION_CATALOGUE_DOCS)
    def get(self, request):
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Retrieved successfully",
            data=PermissionGroupSerializer(
                role_admin_service.permission_catalogue(actor=request.user), many=True
            ).data,
        )


class ChangeStaffRoleView(APIView):
    """Move a staff member to another staff role."""

    permission_classes = [Perm(codenames.STAFF_FULL_ACCESS), IsStrongMFASession]

    @extend_schema(**CHANGE_STAFF_ROLE_DOCS)
    def post(self, request, pk):
        serializer = ChangeStaffRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.select_related("access_role").filter(pk=pk).first()
        role = (
            Role.objects.filter(
                id=serializer.validated_data["role_id"], is_deleted=False
            )
            .prefetch_related("grants")
            .first()
        )
        if user is None:
            raise exceptions.NotFound("Staff member not found.")
        if role is None:
            raise exceptions.ValidationError({"role_id": "Choose a live staff role."})
        user = role_admin_service.change_user_role(
            actor=request.user, user=user, role=role, request=request
        )
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Role changed.",
            data={
                "id": str(user.id),
                "role": user.role,
                "access_role": {"id": str(role.id), "name": role.name},
            },
        )
