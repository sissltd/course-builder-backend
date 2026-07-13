from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters as drf_filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from api.courses.filters import CourseReviewQueueFilter
from api.courses.models import Course
from api.courses.permissions import IsCourseOwner
from api.courses.serializers import (
    CourseCreateSerializer,
    CourseDetailSerializer,
    CourseListSerializer,
    CourseUpdateSerializer,
    ReviewActionSerializer,
    ReviewApproveSerializer,
    ReviewRejectSerializer,
)
from api.courses.services import course_service, review_service
from api.users.permissions import IsAdminRole, IsCourseCreatorRole, IsCreatorReviewerRole

OWNER_SCOPED_ACTIONS = {"retrieve", "update", "partial_update", "destroy", "submit"}


class CourseViewSet(ModelViewSet):
    """A Course Creator's own courses: authoring + submission (SCCS PRD Track A)."""

    permission_classes = [IsCourseCreatorRole]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Course.objects.none()

        queryset = Course.objects.select_related("category")
        if self.action == "publish":
            # Admin-only (enforced by get_permissions) and not owner-scoped:
            # an Admin publishing a course is never its creator.
            return queryset
        return queryset.filter(creator=self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return CourseCreateSerializer
        if self.action in {"update", "partial_update"}:
            return CourseUpdateSerializer
        if self.action == "list":
            return CourseListSerializer
        return CourseDetailSerializer

    def get_permissions(self):
        if self.action in OWNER_SCOPED_ACTIONS:
            return [IsCourseCreatorRole(), IsCourseOwner()]
        if self.action == "publish":
            return [IsAdminRole()]
        return super().get_permissions()

    def perform_destroy(self, instance):
        course_service.delete_draft_course(course=instance, actor=self.request.user)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        course = course_service.submit_course(course=self.get_object(), actor=request.user)
        return Response(CourseDetailSerializer(course, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        course = course_service.publish_course(course=self.get_object(), actor=request.user)
        return Response(CourseDetailSerializer(course, context=self.get_serializer_context()).data)


class CourseReviewViewSet(ReadOnlyModelViewSet):
    """Reviewer queue for Creator Track courses (SCCS PRD Section 11).

    Kept separate from CourseViewSet rather than branching one viewset's
    queryset by role, so a creator's own-courses queryset and a reviewer's
    cross-creator queue never share permission/queryset logic. `list` is
    scoped to Submitted/In Review courses (the actual "queue"); detail actions
    (retrieve/claim/approve/reject) look up any course by id, so acting on a
    course in the wrong status produces a 400 from the service layer rather
    than a misleading 404.
    """

    permission_classes = [IsCreatorReviewerRole | IsAdminRole]
    filterset_class = CourseReviewQueueFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["submitted_at"]
    ordering = ["submitted_at"]

    def get_queryset(self):
        if self.action == "list":
            return course_service.get_review_queue()
        return Course.objects.select_related("category", "creator")

    def get_serializer_class(self):
        if self.action == "list":
            return CourseListSerializer
        return CourseDetailSerializer

    @action(detail=True, methods=["post"])
    def claim(self, request, pk=None):
        course = course_service.claim_for_review(course=self.get_object(), reviewer=request.user)
        return Response(CourseDetailSerializer(course, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        serializer = ReviewApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review_action = review_service.approve_course(
            course=self.get_object(),
            reviewer=request.user,
            feedback=serializer.validated_data["feedback"],
        )
        return Response(ReviewActionSerializer(review_action).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        serializer = ReviewRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review_action = review_service.reject_course(
            course=self.get_object(),
            reviewer=request.user,
            feedback=serializer.validated_data["feedback"],
        )
        return Response(ReviewActionSerializer(review_action).data)
