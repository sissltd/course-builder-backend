from django.urls import path
from rest_framework.routers import DefaultRouter

from api.mie.views import (
    MieDeveloperAdminViewSet,
    MieDeveloperMeView,
    MieDocumentationDownloadView,
    MieDocumentationView,
    MieDeveloperRegistrationView,
    MieSubmissionAdminViewSet,
    MieSubmissionIngestView,
    MieSubmissionQueueView,
    RejectionReasonAdminViewSet,
)

router = DefaultRouter()
router.register(
    r"mie/admin/developers", MieDeveloperAdminViewSet, basename="mie-admin-developers"
)
router.register(
    r"mie/admin/submissions", MieSubmissionAdminViewSet, basename="mie-admin-submissions"
)
router.register(
    r"mie/admin/rejection-reasons",
    RejectionReasonAdminViewSet,
    basename="mie-admin-rejection-reasons",
)

urlpatterns = router.urls + [
    path("mie/v1/register/", MieDeveloperRegistrationView.as_view(), name="mie-developer-register"),
    path("mie/v1/submissions/", MieSubmissionIngestView.as_view(), name="mie-submission-ingest"),
    path("mie/v1/submissions/queue/", MieSubmissionQueueView.as_view(), name="mie-submission-queue"),
    path("mie/v1/me/", MieDeveloperMeView.as_view(), name="mie-developer-me"),
    path("mie/v1/documentation/", MieDocumentationView.as_view(), name="mie-documentation"),
    path(
        "mie/v1/documentation/download/",
        MieDocumentationDownloadView.as_view(),
        name="mie-documentation-download",
    ),
]
