from rest_framework.routers import DefaultRouter

from api.catalog.views import (
    CategoryRequestViewSet,
    CategoryViewSet,
    TopicReservationRequestViewSet,
    TopicViewSet,
)

router = DefaultRouter()
router.register("categories", CategoryViewSet, basename="category")
router.register(
    "category-requests", CategoryRequestViewSet, basename="category-request"
)
router.register("topics", TopicViewSet, basename="topic")
router.register(
    "topic-reservations",
    TopicReservationRequestViewSet,
    basename="topic-reservation",
)

urlpatterns = router.urls
