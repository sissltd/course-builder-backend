from django.utils import timezone
from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import exceptions
from rest_framework import filters as drf_filters
from rest_framework.viewsets import ModelViewSet

from api.collaborators.filters import WorkspaceCollaboratorFilter
from api.collaborators.models import CourseCollaborator, WorkspaceCollaborator
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
            "Supports `search`, `role`, `category`, `date_from`, and `date_to` "
            "filters used by the desktop and mobile designs. Removed people "
            "are hidden from this list by default.\n\n"
            "**Auth:** Course Creator or Writer."
        ),
        tags=["Creator — Collaborators"],
        responses={
            200: OpenApiResponse(response=WorkspaceCollaboratorSerializer(many=True)),
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
            "audit trail stays intact. Active access grants on courses owned "
            "by the caller are revoked at the same time.\n\n"
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
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    filterset_class = WorkspaceCollaboratorFilter
    ordering_fields = ["created_datetime", "role", "invited_email"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return WorkspaceCollaborator.objects.none()
        queryset = WorkspaceCollaborator.objects.filter(
            owner=self.request.user
        ).select_related("user")
        if self.action == "list" and "status" not in self.request.query_params:
            queryset = queryset.exclude(status=WorkspaceCollaborator.Status.REMOVED)
        return queryset

    def perform_create(self, serializer):
        from django.db import IntegrityError

        try:
            serializer.save(owner=self.request.user)
        except IntegrityError as exc:
            raise exceptions.ValidationError(
                "This email is already on your workspace roster."
            ) from exc

    @transaction.atomic
    def perform_destroy(self, instance):
        if instance.status == WorkspaceCollaborator.Status.REMOVED:
            raise exceptions.ValidationError(
                "This workspace collaborator is already removed."
            )
        if instance.user_id:
            CourseCollaborator.objects.filter(
                course__creator=self.request.user,
                user_id=instance.user_id,
            ).delete()

        instance.status = WorkspaceCollaborator.Status.REMOVED
        instance.removed_at = timezone.now()
        instance.save(update_fields=["status", "removed_at", "updated_datetime"])
