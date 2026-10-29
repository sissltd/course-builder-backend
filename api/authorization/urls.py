from django.urls import path

from api.authorization.views.role_admin_views import (
    ChangeStaffRoleView,
    PermissionCatalogueView,
    RoleDetailView,
    RoleListCreateView,
    RoleMembersView,
)

urlpatterns = [
    path("admin/roles/", RoleListCreateView.as_view(), name="admin-roles"),
    path(
        "admin/roles/<uuid:role_id>/",
        RoleDetailView.as_view(),
        name="admin-role-detail",
    ),
    path(
        "admin/roles/<uuid:role_id>/members/",
        RoleMembersView.as_view(),
        name="admin-role-members",
    ),
    path(
        "admin/permissions/",
        PermissionCatalogueView.as_view(),
        name="admin-permissions",
    ),
    path(
        "auth/staff/<uuid:pk>/change-role/",
        ChangeStaffRoleView.as_view(),
        name="staff-change-role",
    ),
]
