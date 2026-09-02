from django.urls import path
from rest_framework.routers import DefaultRouter

from api.courses.views import (
    assessment_views,
    course_appeal_views,
    course_thumbnail_views,
    course_views,
    lesson_sub_resource_views,
    lesson_views,
    module_views,
)

router = DefaultRouter()
router.register("courses", course_views.CourseViewSet, basename="course")
router.register(
    "course-versions", course_views.CourseVersionViewSet, basename="course-version"
)
router.register(
    "review-queue", course_views.CourseReviewViewSet, basename="course-review"
)
router.register(
    "admin/courses", course_views.AdminCourseViewSet, basename="admin-course"
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
    path(
        "courses/<uuid:course_pk>/modules/<uuid:module_pk>/lessons/<uuid:lesson_pk>/content-blocks/",
        lesson_sub_resource_views.LessonContentBlockViewSet.as_view(
            {"get": "list", "post": "create"}
        ),
        name="lesson-content-block-list",
    ),
    path(
        "courses/<uuid:course_pk>/modules/<uuid:module_pk>/lessons/<uuid:lesson_pk>/content-blocks/<uuid:pk>/",
        lesson_sub_resource_views.LessonContentBlockViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="lesson-content-block-detail",
    ),
    path(
        "courses/<uuid:course_pk>/modules/<uuid:module_pk>/lessons/<uuid:lesson_pk>/images/",
        lesson_sub_resource_views.LessonImageViewSet.as_view(
            {"get": "list", "post": "create"}
        ),
        name="lesson-image-list",
    ),
    path(
        "courses/<uuid:course_pk>/modules/<uuid:module_pk>/lessons/<uuid:lesson_pk>/images/<uuid:pk>/",
        lesson_sub_resource_views.LessonImageViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="lesson-image-detail",
    ),
    path(
        "courses/<uuid:course_pk>/modules/<uuid:module_pk>/lessons/<uuid:lesson_pk>/requirements/",
        lesson_sub_resource_views.LessonRequirementViewSet.as_view(
            {"get": "list", "post": "create"}
        ),
        name="lesson-requirement-list",
    ),
    path(
        "courses/<uuid:course_pk>/modules/<uuid:module_pk>/lessons/<uuid:lesson_pk>/requirements/<uuid:pk>/",
        lesson_sub_resource_views.LessonRequirementViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="lesson-requirement-detail",
    ),
    path(
        "courses/<uuid:course_pk>/thumbnail/",
        course_thumbnail_views.CourseThumbnailView.as_view(),
        name="course-thumbnail",
    ),
]
