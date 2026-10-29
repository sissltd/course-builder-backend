from django.urls import path
from rest_framework.routers import DefaultRouter

from api.collaborators.views import (
    collaborator_views,
    invite_views,
    workspace_collaborator_views,
)

router = DefaultRouter()
router.register("course-invites", invite_views.CollaboratorInviteViewSet, basename="collaborator-invite")
router.register(
    "workspace-collaborators",
    workspace_collaborator_views.WorkspaceCollaboratorViewSet,
    basename="workspace-collaborator",
)

urlpatterns = router.urls + [
    path(
        "collaborators/",
        collaborator_views.CourseCollaboratorViewSet.as_view({"get": "list"}),
        name="collaborator-list",
    ),
    path(
        "collaborators/<uuid:pk>/",
        collaborator_views.CourseCollaboratorViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
        ),
        name="collaborator-detail",
    ),
]
