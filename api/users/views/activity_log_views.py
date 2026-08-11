import csv
import io

from django.http import StreamingHttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import filters as drf_filters
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from api.authentication.services import activity_service
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.filters import AdminUserActivityLogFilter, UserActivityLogFilter
from api.users.models import UserActivityLog
from api.users.permissions import IsAdminOrSuperAdminRole
from api.users.serializers.activity_log_serializer import UserActivityLogSerializer
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES


class UserActivityLogListView(ListAPIView):
    """List the current authenticated user's own activity log entries.

    Filterable by ?category= (matches the frontend's tab filters) and/or
    ?action=. Ordered newest-first (the model's own default ordering).
    """

    permission_classes = [IsAuthenticated]
    serializer_class = UserActivityLogSerializer
    filterset_class = UserActivityLogFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["activity_datetime"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return UserActivityLog.objects.none()
        return UserActivityLog.objects.filter(user=self.request.user).select_related(
            "actor_user"
        )


@extend_schema(
    summary="List activity log entries for any user",
    description=(
        "Returns activity log entries across every account, not just the "
        "caller's. This is the audit trail an Admin uses to reconstruct what "
        "happened to an account — logins, lockouts, KYC decisions, course "
        "approvals, moderation actions — which the self-scoped "
        "`/users/me/activity-log/` cannot show them.\n\n"
        "Called from the admin Users screen when opening a user's history, "
        "and from the platform-wide audit view.\n\n"
        "**Auth:** Admin or Super Admin.\n\n"
        "**Prerequisites:** None beyond holding the Admin or Super Admin "
        "role.\n\n"
        "**Important:** Unfiltered this is the whole platform's history and "
        "grows without bound — pass `?user=<uuid>` to scope it to one account, "
        "which is how the UI always calls it. Also filterable by `?category=` "
        "and `?action=`. Ordered newest-first and paginated. Entries are "
        "written by the services that perform each action; nothing writes to "
        "this endpoint."
    ),
    tags=["Admin — Activity Log"],
    responses={
        200: OpenApiResponse(
            response=UserActivityLogSerializer(many=True),
            description="Activity log entries, newest first.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value=[
                        {
                            "id": "c39a7e15-8b42-4f06-9d71-2a5e3c8b0f64",
                            "category": "AUTH",
                            "action": "ACCOUNT_SUSPENDED",
                            "summary": "Suspended chidera.nwosu@example.com.",
                            "actor_user": "ops@soludesks.com",
                            "ip_address": "102.89.34.17",
                            "activity_datetime": "2026-08-06T11:47:12.408Z",
                        }
                    ],
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
)
class AdminUserActivityLogListView(ListAPIView):
    """List activity log entries across all users, for Admins.

    The self-scoped UserActivityLogListView above deliberately stays as it is;
    this is a separate endpoint rather than a role branch inside it, so the
    permission class on each view matches exactly one audience (the swagger
    standard's rule that `permission_classes` and the documented **Auth** line
    agree).
    """

    permission_classes = [IsAdminOrSuperAdminRole]
    serializer_class = UserActivityLogSerializer
    filterset_class = AdminUserActivityLogFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["activity_datetime"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return UserActivityLog.objects.none()
        return UserActivityLog.objects.select_related("user", "actor_user")


def _activity_log_csv_rows(entries):
    """Yield CSV lines for `entries`, header first - one csv.writer row per
    line via an in-memory buffer, the standard Django streaming-CSV idiom."""

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(["category", "action", "summary", "actor", "activity_datetime"])
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    for entry in entries:
        actor = entry.actor_user.email if entry.actor_user_id else ""
        writer.writerow(
            [
                entry.category,
                entry.action,
                entry.summary,
                actor,
                entry.activity_datetime,
            ]
        )
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


class UserActivityLogExportView(APIView):
    """Download the current authenticated user's own activity log as a CSV
    file (the Data & Privacy settings screen's "download activity log").

    Reuses UserActivityLogFilter - the same ?category=/?action= filtering
    the list endpoint supports - rather than re-deriving the query logic.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        base_queryset = UserActivityLog.objects.filter(
            user=request.user
        ).select_related("actor_user")
        queryset = UserActivityLogFilter(request.GET, queryset=base_queryset).qs
        # Materialize before logging the export event itself, so that event
        # never shows up inside the very file it produced.
        entries = list(queryset)

        activity_service.log_activity(
            user=request.user,
            category=UserActivityCategoryEnums.PRIVACY,
            action=UserActivityActionEnums.ACTIVITY_LOG_EXPORTED,
            summary="Exported activity log.",
            request=request,
        )

        response = StreamingHttpResponse(
            _activity_log_csv_rows(entries), content_type="text/csv"
        )
        response["Content-Disposition"] = 'attachment; filename="activity-log.csv"'
        return response
