from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import exceptions
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.authentication.models import UserSession
from api.authentication.serializers import UserSessionSerializer
from api.authentication.services import session_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_SESSION_EXAMPLE = {
    "id": "9b8c7d6e-5f4a-4b3c-2d1e-0f9a8b7c6d5e",
    "ip_address": "102.89.23.14",
    "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "created_datetime": "2026-08-01T09:12:04.000Z",
    "last_seen_at": "2026-08-09T07:45:31.000Z",
    "is_current": True,
}


class UserSessionListView(ListAPIView):
    """List the current authenticated user's active (non-revoked) sessions."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserSessionSerializer

    @extend_schema(
        summary="List active sessions",
        description=(
            "Returns every active (non-revoked) session for the current "
            "authenticated user, most-recently-seen first - used to power "
            "a 'devices logged in' / active sessions screen.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** `is_current` marks the session tied to the "
            "access token used for this request itself."
        ),
        tags=["Auth — Session"],
        responses={
            200: OpenApiResponse(
                response=UserSessionSerializer(many=True),
                description="Active sessions.",
                examples=[OpenApiExample(name="Success", value=[_SESSION_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return session_service.list_active_sessions(user=self.request.user)


class UserSessionRevokeView(APIView):
    """Revoke one of the current authenticated user's own sessions.

    Scoped to the requesting user - a session id belonging to someone else
    (or one that's already revoked) is a 404, never a 403, so this endpoint
    never confirms whether a given session id exists for another account.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Revoke a session",
        description=(
            "Revokes one of the current user's own sessions, e.g. from a "
            "'log out this device' action on the active-sessions "
            "screen.\n\n"
            "**Auth:** Any authenticated user (own sessions only).\n\n"
            "**Prerequisites:** The session id must belong to the caller "
            "and still be active.\n\n"
            "**Important:** A session id belonging to someone else, or one "
            "already revoked, is reported as 404 - never 403 - so this "
            "endpoint never confirms whether a given id exists on another "
            "account."
        ),
        tags=["Auth — Session"],
        request=None,
        responses={
            204: OpenApiResponse(description="Session revoked."),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def delete(self, request, pk):
        try:
            session_service.revoke_session(user=request.user, session_id=pk)
        except UserSession.DoesNotExist as exc:
            raise exceptions.NotFound("Session not found.") from exc
        return Response(status=204)
