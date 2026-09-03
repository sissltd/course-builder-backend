from django.urls import path

from shared.audit.views import AuditLogListView, MyAuditLogExportView

app_name = "audit"
urlpatterns = [
    path("logs", AuditLogListView.as_view(), name="audit-logs"),
    path(
        "users/me/audit-log/export/",
        MyAuditLogExportView.as_view(),
        name="my-audit-log-export",
    ),
]
