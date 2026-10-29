from django.urls import path
from rest_framework.routers import DefaultRouter

from api.reviews import views as review_views

router = DefaultRouter()
router.register(
    "quality-check-criteria",
    review_views.QualityCheckCriterionViewSet,
    basename="quality-check-criterion",
)

urlpatterns = router.urls + [
    path(
        "courses/<uuid:course_pk>/quality-checks/",
        review_views.CourseQualityCheckView.as_view(),
        name="course-quality-checks",
    ),
]
