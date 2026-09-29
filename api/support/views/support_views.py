from drf_spectacular.utils import OpenApiExample, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.generics import ListCreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.decorators import action

from api.authorization import codenames
from api.authorization.permissions import Perm
from api.support.enums import SupportRequestKind
from api.support.models import SupportRequest
from api.support.serializers import (
    SupportContactCreateSerializer,
    SupportRequestSerializer,
    SupportResolveSerializer,
    SupportTitledRequestCreateSerializer,
)
from api.support.services import support_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_REQUEST_EXAMPLE = {
    "id": "0b7c3f5e-6a1d-4e2b-9c8f-2d4a6b8c0e13",
    "kind": "APPEAL",
    "first_name": "Ada",
    "last_name": "Obi",
    "email": "creator@example.com",
    "country": "",
    "title": "My account was suspended in error",
    "web_link": "https://example.com/portfolio",
    "message": "The flagged lesson was original work; please review.",
    "status": "OPEN",
    "due_at": "2026-10-08T11:00:00Z",
    "resolution_notes": "",
    "resolved_at": None,
    "submitted_by_email": "creator@example.com",
    "created_datetime": "2026-09-29T11:00:00Z",
}


@extend_schema(tags=["Public — Support"], auth=[{}])
class SupportContactView(APIView):
    """Contact-us form (Figma Help > Support > Contact us). Public and
    IP-throttled since it creates rows without authentication."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_contact"

    @extend_schema(
        summary="Send a contact message",
        description=(
            "Submits the 'Contact us' form and notifies Support staff in-app.\n\n"
            "Called from the Support page's 'Contact us' action and the "
            "public contact page.\n\n"
            "**Auth:** Public — no credentials required.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** `country` is a two-letter ISO code. Rate-limited "
            "per client IP; exceeding the limit returns 429."
        ),
        request=SupportContactCreateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "first_name": "Ada",
                    "last_name": "Obi",
                    "email": "ada@example.com",
                    "country": "NG",
                    "message": "How do I change my payout bank?",
                },
            ),
        ],
        responses={
            201: SupportRequestSerializer,
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["rate_limited"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = SupportContactCreateSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class _TitledRequestListCreateView(ListCreateAPIView):
    """Shared shape of the ticket and appeal endpoints: a signed-in user
    files a titled request and lists their own. Subclasses set `kind`."""

    kind: str
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SupportRequest.objects.none()
        return SupportRequest.objects.filter(
            kind=self.kind, submitted_by=self.request.user
        ).select_related("submitted_by")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return SupportTitledRequestCreateSerializer
        return SupportRequestSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "kind": self.kind}


@extend_schema_view(
    get=extend_schema(
        summary="List my support tickets",
        description=(
            "Returns the caller's own tickets, newest first, paginated.\n\n"
            "**Auth:** Any signed-in user.\n\n"
            "**Prerequisites:** None."
        ),
        tags=["Support — Tickets"],
        responses={
            200: SupportRequestSerializer(many=True),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    post=extend_schema(
        summary="Create a support ticket",
        description=(
            "Files a ticket and notifies Support staff in-app.\n\n"
            "Called from the Support page's 'Create ticket' action.\n\n"
            "**Auth:** Any signed-in user.\n\n"
            "**Prerequisites:** None."
        ),
        tags=["Support — Tickets"],
        request=SupportTitledRequestCreateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "title": "Cannot upload lesson video",
                    "email": "creator@example.com",
                    "web_link": "https://example.com/screenshot",
                    "description": "The upload stalls at 90%.",
                },
            ),
        ],
        responses={
            201: SupportRequestSerializer,
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class SupportTicketListCreateView(_TitledRequestListCreateView):
    kind = SupportRequestKind.TICKET


@extend_schema_view(
    get=extend_schema(
        summary="List my appeals",
        description=(
            "Returns the caller's own appeals, newest first, paginated.\n\n"
            "**Auth:** Any signed-in user.\n\n"
            "**Prerequisites:** None."
        ),
        tags=["Support — Appeals"],
        responses={
            200: SupportRequestSerializer(many=True),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    post=extend_schema(
        summary="Request an appeal",
        description=(
            "Files a written appeal (PRD: creators can dispute a rejection "
            "or suspension in writing; the decision is final). Notifies "
            "Support staff in-app and sets `due_at` to 7 business days out.\n\n"
            "This is the Figma 'Request for an appeal' form, called from the "
            "Support page's 'Request appeal' action.\n\n"
            "**Auth:** Any signed-in user — including a suspended creator, who "
            "can still sign in to appeal.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Independent of `POST /course-appeals/`, which "
            "disputes one specific rejected course."
        ),
        tags=["Support — Appeals"],
        request=SupportTitledRequestCreateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "title": "My account was suspended in error",
                    "email": "creator@example.com",
                    "web_link": "https://example.com/portfolio",
                    "description": "The flagged lesson was original work.",
                },
            ),
            OpenApiExample(name="Created", response_only=True, value=_REQUEST_EXAMPLE),
        ],
        responses={
            201: SupportRequestSerializer,
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class SupportAppealListCreateView(_TitledRequestListCreateView):
    kind = SupportRequestKind.APPEAL


@extend_schema_view(
    list=extend_schema(
        summary="List support requests (staff queue)",
        description=(
            "Every contact message, ticket and appeal, newest first, "
            "paginated. Filter with `kind` and `status`.\n\n"
            "**Auth:** `support.manage_requests` (Admin and Super Admin by default).\n\n"
            "**Prerequisites:** None."
        ),
        tags=["Admin — Support"],
        responses={
            200: SupportRequestSerializer(many=True),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a support request",
        description=(
            "**Auth:** `support.manage_requests` (Admin and Super Admin by default).\n\n"
            "**Prerequisites:** The request must exist."
        ),
        tags=["Admin — Support"],
        responses={
            200: SupportRequestSerializer,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class SupportRequestAdminViewSet(ReadOnlyModelViewSet):
    """Staff review queue for everything filed from the Support page."""

    permission_classes = [Perm(codenames.SUPPORT_MANAGE_REQUESTS)]
    serializer_class = SupportRequestSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SupportRequest.objects.none()
        queryset = SupportRequest.objects.select_related("submitted_by")
        for field in ("kind", "status"):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value.upper()})
        return queryset

    @extend_schema(
        summary="Resolve a support request",
        description=(
            "Closes an Open request with optional notes and notifies the "
            "submitter in-app. Final: it cannot be resolved twice.\n\n"
            "**Auth:** `support.manage_requests` (Admin and Super Admin by default).\n\n"
            "**Prerequisites:** The request must be `OPEN`."
        ),
        tags=["Admin — Support"],
        request=SupportResolveSerializer,
        responses={
            200: SupportRequestSerializer,
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        serializer = SupportResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resolved = support_service.resolve_request(
            support_request=self.get_object(),
            actor=request.user,
            notes=serializer.validated_data["notes"],
        )
        return Response(SupportRequestSerializer(resolved).data)
