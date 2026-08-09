from django.urls import path
from rest_framework.routers import DefaultRouter

from api.courses.views import (
    assessment_views,
    course_appeal_views,
    course_views,
    lesson_views,
    module_views,
    topic_reservation_views,
    topic_views,
)

router = DefaultRouter()
router.register("topics", topic_views.TopicViewSet, basename="topic")
router.register(
    "topic-reservations",
    topic_reservation_views.TopicReservationRequestViewSet,
    basename="topic-reservation",
)
router.register("courses", course_views.CourseViewSet, basename="course")
router.register(
    "review-queue", course_views.CourseReviewViewSet, basename="course-review"
)
router.register(
    "course-appeals",
    course_appeal_views.CourseAppealViewSet,
    basename="course-appeal",
)

urlpatterns = router.urls + [
    path(
        "courses/<uuid:course_pk>/modules/",
        module_views.ModuleViewSet.as_view({"get": "list", "post": "create"}),
        name="course-module-list",
    ),
    path(
        "courses/<uuid:course_pk>/modules/<uuid:pk>/",
        module_views.ModuleViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="course-module-detail",
    ),
    path(
        "courses/<uuid:course_pk>/modules/<uuid:module_pk>/lessons/",
        lesson_views.LessonViewSet.as_view({"get": "list", "post": "create"}),
        name="module-lesson-list",
    ),
    path(
        "courses/<uuid:course_pk>/modules/<uuid:module_pk>/lessons/<uuid:pk>/",
        lesson_views.LessonViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="module-lesson-detail",
    ),
    path(
        "courses/<uuid:course_pk>/modules/<uuid:module_pk>/lessons/<uuid:lesson_pk>/assessment/",
        assessment_views.LessonAssessmentView.as_view(),
        name="lesson-assessment",
    ),
    path(
        "courses/<uuid:course_pk>/modules/<uuid:module_pk>/assessment/",
        assessment_views.ModuleAssessmentView.as_view(),
        name="module-assessment",
    ),
    path(
        "courses/<uuid:course_pk>/final-assessment/",
        assessment_views.CourseAssessmentView.as_view(),
        name="course-final-assessment",
    ),
]
