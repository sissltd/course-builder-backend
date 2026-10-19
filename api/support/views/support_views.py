from drf_spectacular.utils import extend_schema, extend_schema_view
from django.db.models import Prefetch
from django_filters.rest_framework import DjangoFilterBackend
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListCreateAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.decorators import action

from api.authentication.utils.optional_jwt import OptionalJWTAuthentication
from api.authorization import codenames
from api.authorization.permissions import Perm
from api.support.enums import SupportRequestKind
from api.support.filters import SupportTicketFilter, SupportTicketOrdering
from api.support.models import SupportRequest, SupportTicketMessage
from api.support.docs.support_docs import (
    ADMIN_REQUEST_LIST_DOCS,
    ADMIN_REQUEST_RESOLVE_DOCS,
    ADMIN_REQUEST_RETRIEVE_DOCS,
    ADMIN_TICKET_BULK_STATUS_DOCS,
    ADMIN_TICKET_LIST_DOCS,
    ADMIN_TICKET_REPLY_DOCS,
    ADMIN_TICKET_RETRIEVE_DOCS,
    ADMIN_TICKET_STATS_DOCS,
    ADMIN_TICKET_STATUS_DOCS,
    APPEAL_CREATE_DOCS,
    APPEAL_LIST_DOCS,
    SUPPORT_CONTACT_DOCS,
    TICKET_CREATE_DOCS,
    TICKET_LIST_DOCS,
    TICKET_REPLY_DOCS,
    TICKET_RETRIEVE_DOCS,
)
from api.support.serializers import (
    SupportContactCreateSerializer,
    SupportRequestSerializer,
    SupportResolveSerializer,
    SupportTicketBulkResultSerializer,
    SupportTicketBulkStatusSerializer,
    SupportTicketCreateSerializer,
    SupportTicketDetailSerializer,
    SupportTicketMessageSerializer,
    SupportTicketReplySerializer,
    SupportTicketStatusSerializer,
    SupportTitledRequestCreateSerializer,
)
from api.support.services import support_service, ticket_service


def _ticket_detail_queryset():
    """Tickets with the thread and its authors loaded in two queries."""

    return ticket_service.tickets().prefetch_related(
        Prefetch(
            "messages",
            queryset=SupportTicketMessage.objects.select_related("author"),
        )
    )


class SupportContactView(APIView):
    """Contact-us form (Figma Help > Support > Contact us). Public and
    IP-throttled since it creates rows without authentication. A valid token
    links the message to the caller; a stale one is ignored, never 401."""

    authentication_classes = [OptionalJWTAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support_contact"

    @extend_schema(**SUPPORT_CONTACT_DOCS)
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
    create_serializer_class = SupportTitledRequestCreateSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SupportRequest.objects.none()
        return SupportRequest.objects.filter(
            kind=self.kind, submitted_by=self.request.user
        ).select_related("submitted_by", "assigned_to")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return self.create_serializer_class
        return SupportRequestSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "kind": self.kind}


@extend_schema_view(
    get=extend_schema(**TICKET_LIST_DOCS),
    post=extend_schema(**TICKET_CREATE_DOCS),
)
class SupportTicketListCreateView(_TitledRequestListCreateView):
    kind = SupportRequestKind.TICKET
    create_serializer_class = SupportTicketCreateSerializer
    filterset_fields = ["status", "priority"]


@extend_schema(**TICKET_RETRIEVE_DOCS)
class SupportTicketDetailView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SupportTicketDetailSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SupportRequest.objects.none()
        return _ticket_detail_queryset().filter(submitted_by=self.request.user)


class SupportTicketReplyView(APIView):
    """The submitter's side of the ticket thread."""

    permission_classes = [IsAuthenticated]

    @extend_schema(**TICKET_REPLY_DOCS)
    def post(self, request, pk):
        ticket = get_object_or_404(
            ticket_service.tickets(), pk=pk, submitted_by=request.user
        )
        serializer = SupportTicketReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = ticket_service.add_reply(
            ticket=ticket, author=request.user, body=serializer.validated_data["body"]
        )
        return Response(
            SupportTicketMessageSerializer(message, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    get=extend_schema(**APPEAL_LIST_DOCS),
    post=extend_schema(**APPEAL_CREATE_DOCS),
)
class SupportAppealListCreateView(_TitledRequestListCreateView):
    kind = SupportRequestKind.APPEAL


@extend_schema_view(
    list=extend_schema(**ADMIN_REQUEST_LIST_DOCS),
    retrieve=extend_schema(**ADMIN_REQUEST_RETRIEVE_DOCS),
)
class SupportRequestAdminViewSet(ReadOnlyModelViewSet):
    """Staff review queue for everything filed from the Support page."""

    permission_classes = [Perm(codenames.SUPPORT_MANAGE_REQUESTS)]
    serializer_class = SupportRequestSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SupportRequest.objects.none()
        queryset = SupportRequest.objects.select_related("submitted_by", "assigned_to")
        for field in ("kind", "status"):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value.upper()})
        return queryset

    @extend_schema(**ADMIN_REQUEST_RESOLVE_DOCS)
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


@extend_schema_view(
    list=extend_schema(**ADMIN_TICKET_LIST_DOCS),
    retrieve=extend_schema(**ADMIN_TICKET_RETRIEVE_DOCS),
)
class SupportTicketAdminViewSet(ReadOnlyModelViewSet):
    """Admin Tickets page (Figma): cards, filtered table, Ticket Overview,
    replies and single/bulk status actions."""

    permission_classes = [Perm(codenames.SUPPORT_MANAGE_REQUESTS)]
    filter_backends = [DjangoFilterBackend, SupportTicketOrdering]
    filterset_class = SupportTicketFilter
    ordering_fields = ["created_datetime", "updated_datetime", "priority", "status"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SupportRequest.objects.none()
        if self.action == "retrieve":
            return _ticket_detail_queryset()
        return ticket_service.tickets()

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SupportTicketDetailSerializer
        return SupportRequestSerializer

    @extend_schema(**ADMIN_TICKET_STATS_DOCS)
    @action(detail=False, methods=["get"])
    def stats(self, request):
        return Response(ticket_service.status_counts())

    @extend_schema(**ADMIN_TICKET_STATUS_DOCS)
    @action(detail=True, methods=["post"], url_path="status")
    def change_status(self, request, pk=None):
        serializer = SupportTicketStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = ticket_service.change_status(
            ticket=self.get_object(),
            actor=request.user,
            status=serializer.validated_data["status"],
        )
        return Response(SupportRequestSerializer(ticket).data)

    @extend_schema(**ADMIN_TICKET_BULK_STATUS_DOCS)
    @action(detail=False, methods=["post"], url_path="bulk-status")
    def bulk_status(self, request):
        serializer = SupportTicketBulkStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = ticket_service.bulk_change_status(
            ticket_ids=serializer.validated_data["ids"],
            actor=request.user,
            status=serializer.validated_data["status"],
        )
        return Response(SupportTicketBulkResultSerializer(result).data)

    @extend_schema(**ADMIN_TICKET_REPLY_DOCS)
    @action(detail=True, methods=["post"])
    def messages(self, request, pk=None):
        serializer = SupportTicketReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = ticket_service.add_reply(
            ticket=self.get_object(),
            author=request.user,
            body=serializer.validated_data["body"],
        )
        return Response(
            SupportTicketMessageSerializer(message, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )
