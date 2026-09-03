import csv
import io

from django.http import StreamingHttpResponse
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.views import APIView

from .filters import AuditLogFilter
from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogListView(ListAPIView):
    """
    The permission is set to IsAuthenticated as only authenticated users will be able to see the log of their sessions
    """

    serializer_class = AuditLogSerializer
    permission_classes = (IsAdminUser,)
    filterset_class = AuditLogFilter

    # modifying the queryset to get all Audit log by filtering it by -created_at (that is, from the newest to the oldest)
    def get_queryset(self):
        return AuditLog.objects.all().order_by("-created_at")

    # extending the openapi(swagger) schema, creating filtering logic by setting required to false
    @extend_schema(
        summary="List audit log enteries",
        description="Paginated audit Logs. Filterable by email, event, date range. Authentication required (JWT)",
        parameters=[
            OpenApiParameter("email", str, OpenApiParameter.QUERY, required=False),
            OpenApiParameter(
                "event",
                str,
                OpenApiParameter.QUERY,
                required=False,
                enum=["OTP_REQUESTED", "OTP_VERIFIED", "OTP_FAILED", "OTP_LOCKED"],
            ),
            OpenApiParameter("from_date", str, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("to_date", str, OpenApiParameter.QUERY, required=False),
        ],
        tags=["Admin — Audit"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


def _audit_log_csv_rows(entries):
    """Yield CSV lines for `entries`, header first.

    Same streaming idiom as the activity-log export in
    api/users/views/activity_log_views.py - one csv.writer row per line
    through an in-memory buffer, so a long history never materializes as
    one string.
    """

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(["event", "email", "ip_address", "user_agent", "created_at"])
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    for entry in entries:
        writer.writerow(
            [
                entry.event,
                entry.email,
                entry.ip_address or "",
                entry.user_agent,
                entry.created_at,
            ]
        )
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


class MyAuditLogExportView(APIView):
    """Download the caller's own audit-trail entries as CSV.

    Backs the second download button on the Data and Privacy settings
    screen. Deliberately self-scoped by email rather than reusing the
    admin list view: this is the caller's own data, so it needs no
    elevated role, and no query parameter can widen it to anyone else.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Export my audit trail as CSV",
        request=None,
        responses={
            (200, "text/csv"): OpenApiResponse(
                description="CSV file of the caller's own audit entries."
            )
        },
        description=(
            "Downloads every logged platform action attributed to the "
            "caller's account as a CSV file - the Data and Privacy "
            "screen's 'Download audit trail entries'.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Scoped to the caller's own email server side; "
            "no parameter can widen it to another account. Oldest entry "
            "first. Responds with `text/csv` as an attachment, not JSON. "
            "Administrators wanting the platform-wide trail use "
            "`GET /api/v1/logs` instead."
        ),
        tags=["Creator — Activity Log"],
    )
    def get(self, request):
        entries = list(
            AuditLog.objects.filter(email__iexact=request.user.email).order_by(
                "created_at"
            )
        )
        response = StreamingHttpResponse(
            _audit_log_csv_rows(entries), content_type="text/csv"
        )
        response["Content-Disposition"] = 'attachment; filename="audit-trail.csv"'
        return response
