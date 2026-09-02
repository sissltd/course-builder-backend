from rest_framework.routers import DefaultRouter

from api.catalog.views import (
    CategoryRequestViewSet,
    ActiveTopicReservationViewSet,
    AdminTopicReservationRequestViewSet,
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
router.register(
    "admin/reservations/requests",
    AdminTopicReservationRequestViewSet,
    basename="admin-reservation-request",
)
router.register(
    "admin/reservations/active",
    ActiveTopicReservationViewSet,
    basename="admin-active-reservation",
)

urlpatterns = router.urls
