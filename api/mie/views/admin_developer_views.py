from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from api.mie.enums import DeveloperAccountStatus, DeveloperJourneyStage, MiePlanType
from api.mie.filters import AdminDeveloperFilterSet
from api.mie.models import DeveloperAccount, WebhookEndpoint
from api.mie.serializers.developer_admin_serializer import (
    DeveloperAccountAdminSerializer,
    DeveloperActionResponseSerializer,
    DeveloperApprovalResponseSerializer,
    DeveloperRegisterSerializer,
    VideoAssignmentSettingSerializer,
)
from api.mie.services import developer_journey_service, developer_service
from api.authorization import codenames
from api.authorization.permissions import Perm
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES


@extend_schema(tags=["Admin — MIE Developers"])
class MieDeveloperAdminViewSet(viewsets.ReadOnlyModelViewSet):
    """Superadmin lifecycle management for external MIE developers.

    Registration creates a PENDING account; approval issues the API key
    that is shown exactly once in the approve response. Rejection is
    reachable from any state (decisions are reversible) and revokes key
    material; re-approval issues fresh credentials.
    """

    queryset = DeveloperAccount.objects.order_by("-created_datetime").prefetch_related(
        Prefetch(
            "webhook_endpoints",
            queryset=WebhookEndpoint.objects.filter(is_deleted=False),
            to_attr="live_webhook_endpoints",
        )
    )
    serializer_class = DeveloperAccountAdminSerializer
    permission_classes = [Perm(codenames.MIE_MANAGE_CONSOLE)]
    lookup_field = "id"
    filterset_class = AdminDeveloperFilterSet
    search_fields = ["email"]

    def get_queryset(self):
        # Only reads carry the journey annotations: an action changes the
        # account after loading it, and its response works the stage out
        # afresh (developer_journey_service.journey_payload).
        queryset = super().get_queryset()
        if self.action in {"list", "retrieve"}:
            return developer_journey_service.with_journey(queryset)
        return queryset

    @extend_schema(
        summary="List developers",
        description=(
            "Returns a paginated directory of every external MIE developer "
            "account on the platform. Each row shows status, plan type, "
            "and a masked key preview \u2014 never the raw key material.\n\n"
            "Called from the superadmin MIE Developers table to get an "
            "overview of all registered developers and filter or search "
            "the list.\n\n"
            "**Auth:** The `mie.manage_console` permission — Super Admin by default.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Results are paginated. Use `?status=`, "
            "`?plan_type=` and `?journey_stage=` to narrow results; use "
            "`?search=` to filter by email substring. `journey` is worked "
            "out from the developer's ideas and courses on every read: the "
            "furthest stage any of them has reached. The `api_key_preview` "
            "field is always masked or null \u2014 the raw key is never "
            "exposed here."
        ),
        parameters=[
            OpenApiParameter(
                name="status",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=[s.value for s in DeveloperAccountStatus],
                description="Filter by account lifecycle status.",
            ),
            OpenApiParameter(
                name="plan_type",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=[p.value for p in MiePlanType],
                description="Filter by payout plan type.",
            ),
            OpenApiParameter(
                name="journey_stage",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=[stage.value for stage in DeveloperJourneyStage],
                description=(
                    "Filter by journey stage: the furthest any of the "
                    "developer's ideas or courses has reached. An unknown "
                    "value is a 400."
                ),
            ),
            OpenApiParameter(
                name="search",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Substring match on the developer's email address.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=DeveloperAccountAdminSerializer(many=True),
                description="Paginated list of developer accounts.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value=[
                            {
                                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                "email": "dev@studio.io",
                                "webhook_endpoints": [
                                    {
                                        "id": "5b1f0c9e-2d4a-4f7e-9a3b-8c6d1e2f3a4b",
                                        "url": "https://hooks.studio.io/mie",
                                        "events": ["all"],
                                        "created_datetime": "2026-08-23T08:55:00Z",
                                        "updated_datetime": "2026-08-23T08:55:00Z",
                                    }
                                ],
                                "status": "APPROVED",
                                "plan_type": "PAID_PER_SUBMISSION",
                                "journey": {
                                    "stage": "COURSE_DELIVERED",
                                    "label": "Course Delivered",
                                    "step": 5,
                                    "total_steps": 8,
                                    "next_step": (
                                        "Your course is in content review. On "
                                        "COURSE_REVISION_REQUESTED, fix it and deliver it "
                                        "again; otherwise wait for COURSE_TEXT_APPROVED."
                                    ),
                                    "ideas_submitted": 3,
                                    "ideas_approved": 1,
                                    "courses_delivered": 1,
                                    "courses_published": 0,
                                },
                                "source_type": "EXTERNAL",
                                "api_key_preview": "scb_live_a1b2c3d4...",
                                "api_key_issued_at": "2026-07-01T10:00:00Z",
                                "api_key_last_used_at": "2026-07-15T08:32:11Z",
                                "decided_at": "2026-07-01T10:00:00Z",
                                "created_datetime": "2026-06-28T14:22:00Z",
                                "updated_datetime": "2026-07-01T10:00:00Z",
                            }
                        ],
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Retrieve a developer",
        description=(
            "Returns the full admin representation of a single developer "
            "account, including key issuance and last-use timestamps, "
            "decision history fields, and the masked API key preview.\n\n"
            "Called when a superadmin opens a developer's detail panel to "
            "review their account history before taking an approval "
            "action.\n\n"
            "**Auth:** The `mie.manage_console` permission — Super Admin by default.\n\n"
            "**Prerequisites:** The developer account must exist.\n\n"
            "**Important:** The raw API key is never included. Only the "
            "masked preview prefix is returned."
        ),
        responses={
            200: OpenApiResponse(
                response=DeveloperAccountAdminSerializer,
                description="The requested developer account.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                            "email": "dev@studio.io",
                            "webhook_endpoints": [
                                    {
                                        "id": "5b1f0c9e-2d4a-4f7e-9a3b-8c6d1e2f3a4b",
                                        "url": "https://hooks.studio.io/mie",
                                        "events": ["all"],
                                        "created_datetime": "2026-08-23T08:55:00Z",
                                        "updated_datetime": "2026-08-23T08:55:00Z",
                                    }
                                ],
                            "status": "PENDING",
                            "plan_type": "PAID_PER_SUBMISSION",
                            "api_key_preview": None,
                            "api_key_issued_at": None,
                            "api_key_last_used_at": None,
                            "decided_at": None,
                            "created_datetime": "2026-06-28T14:22:00Z",
                            "updated_datetime": "2026-06-28T14:22:00Z",
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        summary="Register a developer manually",
        description=(
            "Superadmin-side account creation for manually onboarding an "
            "external MIE developer. The account lands in PENDING status "
            "with no API key issued yet \u2014 an explicit approve action is "
            "required before the developer can authenticate.\n\n"
            "This mirrors the self-service path "
            "(POST /api/v1/mie/v1/register/) that developers use to "
            "register themselves; both land in PENDING and require "
            "approval through this admin surface.\n\n"
            "**Auth:** The `mie.manage_console` permission — Super Admin by default.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** A duplicate email address will return 400. "
            "The account remains in PENDING until approve is called \u2014 "
            "no credentials are issued at registration."
        ),
        request=DeveloperRegisterSerializer,
        examples=[
            OpenApiExample(
                name="Manual onboarding",
                request_only=True,
                value={
                    "email": "dev@studio.io",
                    "webhook_url": "https://hooks.studio.io/mie",
                    "plan_type": "PAID_PER_SUBMISSION",
                },
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=DeveloperAccountAdminSerializer,
                description="Developer account created in PENDING status.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                            "email": "dev@studio.io",
                            "webhook_endpoints": [
                                    {
                                        "id": "5b1f0c9e-2d4a-4f7e-9a3b-8c6d1e2f3a4b",
                                        "url": "https://hooks.studio.io/mie",
                                        "events": ["all"],
                                        "created_datetime": "2026-08-23T08:55:00Z",
                                        "updated_datetime": "2026-08-23T08:55:00Z",
                                    }
                                ],
                            "status": "PENDING",
                            "plan_type": "PAID_PER_SUBMISSION",
                            "api_key_preview": None,
                            "api_key_issued_at": None,
                            "api_key_last_used_at": None,
                            "decided_at": None,
                            "created_datetime": "2026-06-28T14:22:00Z",
                            "updated_datetime": "2026-06-28T14:22:00Z",
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def create(self, request, *args, **kwargs):
        serializer = DeveloperRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = developer_service.register_developer(**serializer.validated_data)
        return Response(
            DeveloperAccountAdminSerializer(account).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Approve a developer",
        description=(
            "Approves a PENDING, REJECTED, or SUSPENDED developer account "
            "and issues a fresh API key. The full key is returned exactly "
            "once in the response and can never be retrieved afterwards \u2014 "
            "store it immediately on the client side.\n\n"
            "This is the documentation delivery moment: from approval "
            "onwards the developer's integration docs are live at "
            "GET /api/v1/mie/v1/documentation/ and also accessible via "
            "the /me endpoint at any time.\n\n"
            "**Auth:** The `mie.manage_console` permission — Super Admin by default.\n\n"
            "**Prerequisites:** The developer account must exist and not "
            "already be in APPROVED status.\n\n"
            "**Important:** The raw API key is shown exactly once. If "
            "the key is lost, the developer must be suspended and "
            "re-approved to receive a new one. Approving an already "
            "approved account returns 400."
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                response=DeveloperApprovalResponseSerializer,
                description="Developer approved with one-time API key.",
                examples=[
                    OpenApiExample(
                        name="Fresh key issued",
                        value={
                            "account": {
                                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                "email": "dev@studio.io",
                                "webhook_endpoints": [
                                    {
                                        "id": "5b1f0c9e-2d4a-4f7e-9a3b-8c6d1e2f3a4b",
                                        "url": "https://hooks.studio.io/mie",
                                        "events": ["all"],
                                        "created_datetime": "2026-08-23T08:55:00Z",
                                        "updated_datetime": "2026-08-23T08:55:00Z",
                                    }
                                ],
                                "status": "APPROVED",
                                "plan_type": "PAID_PER_SUBMISSION",
                                "source_type": "EXTERNAL",
                                "api_key_preview": "scb_live_a1b2c3d4...",
                                "api_key_issued_at": "2026-07-01T10:00:00Z",
                                "api_key_last_used_at": None,
                                "decided_at": "2026-07-01T10:00:00Z",
                                "created_datetime": "2026-06-28T14:22:00Z",
                                "updated_datetime": "2026-07-01T10:00:00Z",
                            },
                            "one_time_api_key": "scb_live_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
                        },
                    ),
                    OpenApiExample(
                        name="Existing key retained",
                        value={
                            "account": {
                                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                "email": "dev@studio.io",
                                "webhook_endpoints": [
                                    {
                                        "id": "5b1f0c9e-2d4a-4f7e-9a3b-8c6d1e2f3a4b",
                                        "url": "https://hooks.studio.io/mie",
                                        "events": ["all"],
                                        "created_datetime": "2026-08-23T08:55:00Z",
                                        "updated_datetime": "2026-08-23T08:55:00Z",
                                    }
                                ],
                                "status": "APPROVED",
                                "plan_type": "PAID_PER_SUBMISSION",
                                "source_type": "EXTERNAL",
                                "api_key_preview": "scb_live_a1b2c3d4...",
                                "api_key_issued_at": "2026-07-01T10:00:00Z",
                                "api_key_last_used_at": "2026-07-15T08:32:11Z",
                                "decided_at": "2026-07-15T09:00:00Z",
                                "created_datetime": "2026-06-28T14:22:00Z",
                                "updated_datetime": "2026-07-15T09:00:00Z",
                            },
                            "one_time_api_key": None,
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def approve(self, request, id=None):
        account = self.get_object()
        raw_key = developer_service.approve_developer(actor=request.user, account=account)
        return Response(
            {
                "account": DeveloperAccountAdminSerializer(account).data,
                "one_time_api_key": raw_key,
            }
        )

    @extend_schema(
        summary="Reject a developer",
        description=(
            "Rejects a developer account from any lifecycle state and "
            "revokes its credentials immediately. The developer can no "
            "longer authenticate or receive webhook events.\n\n"
            "Use this when a registration should be denied or an existing "
            "account needs to be permanently shut down. The decision is "
            "reversible \u2014 a subsequent approve call issues fresh keys.\n\n"
            "**Auth:** The `mie.manage_console` permission — Super Admin by default.\n\n"
            "**Prerequisites:** The developer account must exist.\n\n"
            "**Important:** Rejecting revokes the API key immediately \u2014 "
            "any in-flight requests using the key will fail. This action "
            "is reversible via approve, which issues new credentials."
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                response=DeveloperActionResponseSerializer,
                description="Developer rejected and credentials revoked.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "detail": "dev@studio.io rejected; credentials revoked."
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def reject(self, request, id=None):
        account = self.get_object()
        developer_service.reject_developer(actor=request.user, account=account)
        return Response(
            {"detail": f"{account.email} rejected; credentials revoked."}
        )

    @extend_schema(
        summary="Suspend a developer",
        description=(
            "Freezes an APPROVED developer account so its API key stops "
            "working immediately. The account's queue history, webhook "
            "configuration, and all other data are retained untouched.\n\n"
            "Use this for temporary access revocation without losing any "
            "integration state. The account can be restored to full "
            "access later via the approve action.\n\n"
            "**Auth:** The `mie.manage_console` permission — Super Admin by default.\n\n"
            "**Prerequisites:** The developer account must exist and be "
            "in APPROVED status.\n\n"
            "**Important:** Suspension is immediate \u2014 in-flight API "
            "requests using the frozen key will be rejected. Queue "
            "history is preserved so no submission data is lost. Only "
            "APPROVED accounts can be suspended; attempting to suspend "
            "a PENDING, REJECTED, or SUSPENDED account returns 400."
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                response=DeveloperActionResponseSerializer,
                description="Developer account suspended.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "detail": "dev@studio.io suspended."
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def suspend(self, request, id=None):
        account = self.get_object()
        developer_service.suspend_developer(actor=request.user, account=account)
        return Response({"detail": f"{account.email} suspended."})


class MieDeveloperVideoAssignmentSettingView(APIView):
    """Turn video assignment on or off for one developer account."""

    permission_classes = [Perm(codenames.MIE_MANAGE_CONSOLE)]

    @extend_schema(
        tags=["Admin — MIE Developers"],
        summary="Set whether a developer's videos need assigning",
        description=(
            "Turns video assignment on or off for an external developer. "
            "With it on, once the text of one of their courses passes the "
            "first review seat the course waits with no video producer "
            "until an admin assigns one (POST /api/v1/mie/admin/submissions/"
            "{submission_id}/course/video-assignment/). With it off - the "
            "default - the developer sends the video as soon as the text "
            "passes.\n\n"
            "Called when onboarding a developer, such as a production "
            "studio, who should only make a video when the platform asks.\n\n"
            "**Auth:** The `mie.manage_console` permission — Super Admin by default.\n\n"
            "**Prerequisites:** The account exists and is not the platform's "
            "crawler (SYSTEM) account.\n\n"
            "**Important:** Read when a course's text is approved, so a "
            "course already past that point keeps the producer it has. "
            "Setting the value the account already has changes nothing. No "
            "webhook fires; the developer sees the value on GET /mie/v1/me/."
        ),
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description="The developer account id.",
            )
        ],
        request=VideoAssignmentSettingSerializer,
        examples=[
            OpenApiExample(
                name="Require assignment",
                request_only=True,
                value={"requires_video_assignment": True},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=DeveloperAccountAdminSerializer,
                description="The account with the setting applied.",
                examples=[
                    OpenApiExample(
                        name="Assignment required",
                        value={
                            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                            "email": "dev@studio.io",
                            "webhook_endpoints": [
                                {
                                    "id": "5b1f0c9e-2d4a-4f7e-9a3b-8c6d1e2f3a4b",
                                    "url": "https://hooks.studio.io/mie",
                                    "events": ["all"],
                                    "created_datetime": "2026-08-23T08:55:00Z",
                                    "updated_datetime": "2026-08-23T08:55:00Z",
                                }
                            ],
                            "status": "APPROVED",
                            "plan_type": "PAID_PER_SUBMISSION",
                            "requires_video_assignment": True,
                            "source_type": "EXTERNAL",
                            "api_key_preview": "scb_live_a1b2c3d...",
                            "api_key_issued_at": "2026-08-23T09:00:00Z",
                            "api_key_last_used_at": "2026-08-24T15:30:00Z",
                            "decided_at": "2026-08-23T09:00:00Z",
                            "created_datetime": "2026-08-23T08:55:00Z",
                            "updated_datetime": "2026-10-28T10:00:00Z",
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, id):
        account = get_object_or_404(DeveloperAccount, pk=id)
        serializer = VideoAssignmentSettingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = developer_service.set_video_assignment_required(
            actor=request.user,
            account=account,
            required=serializer.validated_data["requires_video_assignment"],
        )
        return Response(DeveloperAccountAdminSerializer(account).data)
