from django.urls import path
from rest_framework.routers import DefaultRouter

from api.support.views.support_views import (
    SupportAppealListCreateView,
    SupportContactView,
    SupportRequestAdminViewSet,
    SupportTicketAdminViewSet,
    SupportTicketDetailView,
    SupportTicketListCreateView,
    SupportTicketReplyView,
)

router = DefaultRouter()
router.register(
    "support/requests", SupportRequestAdminViewSet, basename="support-request"
)
router.register(
    "support/admin/tickets", SupportTicketAdminViewSet, basename="support-admin-ticket"
)

urlpatterns = [
    path("support/contact/", SupportContactView.as_view(), name="support-contact"),
    path(
        "support/tickets/",
        SupportTicketListCreateView.as_view(),
        name="support-ticket-list",
    ),
    path(
        "support/tickets/<uuid:pk>/",
        SupportTicketDetailView.as_view(),
        name="support-ticket-detail",
    ),
    path(
        "support/tickets/<uuid:pk>/messages/",
        SupportTicketReplyView.as_view(),
        name="support-ticket-reply",
    ),
    path(
        "support/appeals/",
        SupportAppealListCreateView.as_view(),
        name="support-appeal-list",
    ),
] + router.urls
