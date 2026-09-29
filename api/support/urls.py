from django.urls import path
from rest_framework.routers import DefaultRouter

from api.support.views.support_views import (
    SupportAppealListCreateView,
    SupportContactView,
    SupportRequestAdminViewSet,
    SupportTicketListCreateView,
)

router = DefaultRouter()
router.register(
    "support/requests", SupportRequestAdminViewSet, basename="support-request"
)

urlpatterns = [
    path("support/contact/", SupportContactView.as_view(), name="support-contact"),
    path(
        "support/tickets/",
        SupportTicketListCreateView.as_view(),
        name="support-ticket-list",
    ),
    path(
        "support/appeals/",
        SupportAppealListCreateView.as_view(),
        name="support-appeal-list",
    ),
] + router.urls
