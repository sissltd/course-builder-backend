from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from api.courses.models import CategoryRequest
from api.courses.serializers import (
    CategoryRequestApproveSerializer,
    CategoryRequestCreateSerializer,
    CategoryRequestSerializer,
)
from api.courses.services import category_request_service
from api.users.enums import UserRole
from api.users.permissions import IsAdminRole, IsCourseCreatorRole, IsCreatorReviewerRole

MANAGE_ACTIONS = {"approve", "reject"}


class CategoryRequestViewSet(ModelViewSet):
    """A creator's requests for a new Category, plus Admin/Reviewer approve/reject.

    Admins and Creator Reviewers see every request (to review the queue) -
    Reviewers manage Category/Topic pricing directly (full CRUD parity with
    Admin), so they also need to approve/reject the requests that create new
    Categories. Creators see only their own, mirroring CourseViewSet's
    creator-scoping pattern.
    """

    permission_classes = [IsCourseCreatorRole | IsAdminRole | IsCreatorReviewerRole]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CategoryRequest.objects.none()

        queryset = CategoryRequest.objects.select_related(
            "requested_by", "resulting_category"
        )
        if self.request.user.is_superuser or self.request.user.role in (
            UserRole.ADMIN,
            UserRole.CREATOR_REVIEWER,
        ):
            return queryset
        return queryset.filter(requested_by=self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return CategoryRequestCreateSerializer
        if self.action == "approve":
            # Also drives OpenAPI schema generation for the approve action,
            # since it doesn't call self.get_serializer() at runtime (it
            # instantiates CategoryRequestApproveSerializer directly below).
            return CategoryRequestApproveSerializer
        return CategoryRequestSerializer

    def get_permissions(self):
        if self.action in MANAGE_ACTIONS:
            return [(IsAdminRole | IsCreatorReviewerRole)()]
        return super().get_permissions()

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        serializer = CategoryRequestApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        category_request = category_request_service.approve_request(
            category_request=self.get_object(),
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(CategoryRequestSerializer(category_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        category_request = category_request_service.reject_request(
            category_request=self.get_object(), actor=request.user
        )
        return Response(CategoryRequestSerializer(category_request).data)
