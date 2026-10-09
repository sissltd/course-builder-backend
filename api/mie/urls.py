from django.urls import path
from rest_framework.routers import DefaultRouter

from api.mie.views import (
    MieCoursePushView,
    MieCourseVideoView,
    MieCourseRequirementsView,
    MieUploadPresignView,
    MieDeveloperAdminViewSet,
    MieDeveloperMeView,
    MieDocumentationDownloadView,
    MieDocumentationView,
    MieDeveloperRegistrationView,
    MieSubmissionAdminViewSet,
    MieSubmissionIngestView,
    MieSubmissionQueueView,
    MieWebhookEndpointDetailView,
    MieWebhookEndpointListView,
    MieWebhookEventTypesView,
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
    path(
        "mie/v1/submissions/<uuid:submission_id>/course/",
        MieCoursePushView.as_view(),
        name="mie-course-push",
    ),
    path(
        "mie/v1/submissions/<uuid:submission_id>/course/video/",
        MieCourseVideoView.as_view(),
        name="mie-course-video",
    ),
    path(
        "mie/v1/course-requirements/",
        MieCourseRequirementsView.as_view(),
        name="mie-course-requirements",
    ),
    path("mie/v1/uploads/presign/", MieUploadPresignView.as_view(), name="mie-upload-presign"),
    path("mie/v1/me/", MieDeveloperMeView.as_view(), name="mie-developer-me"),
    path(
        "mie/v1/webhooks/",
        MieWebhookEndpointListView.as_view(),
        name="mie-webhook-endpoint-list",
    ),
    path(
        "mie/v1/webhooks/event-types/",
        MieWebhookEventTypesView.as_view(),
        name="mie-webhook-event-types",
    ),
    path(
        "mie/v1/webhooks/<uuid:endpoint_id>/",
        MieWebhookEndpointDetailView.as_view(),
        name="mie-webhook-endpoint-detail",
    ),
    path("mie/v1/documentation/", MieDocumentationView.as_view(), name="mie-documentation"),
    path(
        "mie/v1/documentation/download/",
        MieDocumentationDownloadView.as_view(),
        name="mie-documentation-download",
    ),
]
