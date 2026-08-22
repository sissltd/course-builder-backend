from rest_framework.routers import DefaultRouter

from api.catalog.views import (
    CategoryViewSet,
    TopicReservationRequestViewSet,
    TopicViewSet,
)

router = DefaultRouter()
router.register("categories", CategoryViewSet, basename="category")
router.register("topics", TopicViewSet, basename="topic")
router.register(
    "topic-reservations",
    TopicReservationRequestViewSet,
    basename="topic-reservation",
)

urlpatterns = router.urls
