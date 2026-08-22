from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import exceptions
from rest_framework.viewsets import ModelViewSet

from api.collaborators.models import WorkspaceCollaborator
from api.collaborators.serializers import WorkspaceCollaboratorSerializer
from api.users.permissions import IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES


@extend_schema_view(
    list=extend_schema(
        summary="List my workspace collaborators",
        description=(
            "Returns the caller's account-level team roster - the "
            "'Collaborators' sidebar page - with each person's "
            "platform-wide role and invite status. Not course-scoped: this "
            "is everyone the caller works with across their workspace.\n\n"
            "**Auth:** Course Creator or Writer."
        ),
        tags=["Creator — Collaborators"],
        responses={
            200: OpenApiResponse(
                response=WorkspaceCollaboratorSerializer(many=True)
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Invite someone to my workspace",
        description=(
            "Adds a person to the caller's roster by email (they need no "
            "account yet) with a platform-wide role of ADMIN, AUTHOR, or "
            "COLLABORATOR. Duplicate emails per workspace are rejected.\n\n"
            "**Auth:** Course Creator or Writer."
        ),
        tags=["Creator — Collaborators"],
        request=WorkspaceCollaboratorSerializer,
        responses={
            201: OpenApiResponse(response=WorkspaceCollaboratorSerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    partial_update=extend_schema(
        summary="Update a workspace collaborator",
        description=(
            "Updates role or demographic fields for one roster entry.\n\n"
            "**Auth:** Course Creator or Writer (roster owner)."
        ),
        tags=["Creator — Collaborators"],
        request=WorkspaceCollaboratorSerializer,
        responses={
            200: OpenApiResponse(response=WorkspaceCollaboratorSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    destroy=extend_schema(
        summary="Remove a workspace collaborator",
        description=(
            "Marks the roster entry REMOVED (history is kept - removed_at "
            "is stamped, the row is not deleted) so past course "
            "assignments and audit trails stay intact.\n\n"
            "**Auth:** Course Creator or Writer (roster owner)."
        ),
        tags=["Creator — Collaborators"],
        responses={
            204: OpenApiResponse(description="Collaborator removed."),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class WorkspaceCollaboratorViewSet(ModelViewSet):
    """The account-level 'Collaborators' sidebar roster.

    Distinct from course collaborators: rows live on the owner's account,
    not on any course. All actions are scoped to the caller's own roster -
    another creator's workspace entries 404 rather than leaking existence.
    Removal is a soft REMOVED status, per the target schema's design.
    """

    serializer_class = WorkspaceCollaboratorSerializer
    permission_classes = [IsCourseCreatorRole]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return WorkspaceCollaborator.objects.none()
        return WorkspaceCollaborator.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        from django.db import IntegrityError

        try:
            serializer.save(owner=self.request.user)
        except IntegrityError as exc:
            raise exceptions.ValidationError(
                "This email is already on your workspace roster."
            ) from exc

    def perform_destroy(self, instance):
        if instance.status == WorkspaceCollaborator.Status.REMOVED:
            raise exceptions.ValidationError(
                "This workspace collaborator is already removed."
            )
        instance.status = WorkspaceCollaborator.Status.REMOVED
        instance.removed_at = timezone.now()
        instance.save(update_fields=["status", "removed_at", "updated_datetime"])
