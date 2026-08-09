from django.urls import path

from api.collaborators.views import collaborator_views

urlpatterns = [
    path(
        "collaborators/",
        collaborator_views.CourseCollaboratorViewSet.as_view(
            {"get": "list", "post": "create"}
        ),
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
