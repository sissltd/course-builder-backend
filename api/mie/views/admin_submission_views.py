from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import exceptions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from api.mie.filters import AdminSubmissionFilterSet
from api.mie.models import CourseSubmission, SubmissionRejectionReason
from api.mie.serializers.admin_submission_serializer import (
    AdminSubmissionSerializer,
    DemandSignalsSerializer,
    PayoutBypassSerializer,
    SubmissionDecisionResponseSerializer,
    SubmissionDecisionSerializer,
)
from api.mie.services import submission_admin_service
from api.users.permissions import IsSuperAdminRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES


ADMIN_QUEUE_PARAMETERS = [
    OpenApiParameter(
        name="developer",
        type=OpenApiTypes.UUID,
        required=False,
        description="Filter to one developer account id.",
    ),
    OpenApiParameter(
        name="email",
        type=str,
        required=False,
        description="Filter to one developer account by exact email.",
    ),
    OpenApiParameter(
        name="status",
        type=str,
        required=False,
        description="One pipeline state (PENDING_REVIEW, DUPLICATE_IN_QUEUE, "
        "DUPLICATE_EXISTING, PREVIOUSLY_REJECTED, APPROVED, REJECTED).",
    ),
    OpenApiParameter(
        name="payout_bypass",
        type=bool,
        required=False,
        description="Filter to bypassed (true) or paying (false) ideas.",
    ),
    OpenApiParameter(
        name="created_after",
        type=str,
        required=False,
        description="ISO-8601 lower bound on arrival time.",
    ),
    OpenApiParameter(
        name="created_before",
        type=str,
        required=False,
        description="ISO-8601 upper bound on arrival time.",
    ),
    OpenApiParameter(
        name="search",
        type=str,
        required=False,
        description="Case-insensitive substring match on title or developer email.",
    ),
]


@extend_schema(tags=["Admin — MIE Submissions"])
class MieSubmissionAdminViewSet(viewsets.ReadOnlyModelViewSet):
    """Superadmin queue over every developer's submissions."""

    queryset = (
        CourseSubmission.objects.select_related(
            "developer", "rejection_reason", "decided_by"
        ).order_by("-created_datetime")
    )
    serializer_class = AdminSubmissionSerializer
    permission_classes = [IsSuperAdminRole]
    filterset_class = AdminSubmissionFilterSet
    lookup_field = "id"

    @extend_schema(
        summary="List all submissions",
        description=(
            "Returns every submission from every developer across the "
            "platform, ordered newest-first. This is the superadmin's "
            "primary review queue — the place to find ideas that need a "
            "decision, track recently approved or rejected submissions, "
            "and audit the full pipeline.\n\n"
            "Call this endpoint at the start of each admin review session "
            "to pull the latest queue state, or refresh after deciding on "
            "a submission to see the updated pipeline.\n\n"
            "**Auth:** Superadmin role required.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Results are ordered newest-first by default. "
            "Use the search and filter parameters to narrow the queue; "
            "unfiltered responses may be large for active marketplaces. "
            "The `search` parameter performs a case-insensitive substring "
            "match across title and developer email fields."
        ),
        parameters=ADMIN_QUEUE_PARAMETERS,
        responses={
            200: OpenApiResponse(
                response=AdminSubmissionSerializer(many=True),
                description="List of submissions in the admin queue.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value=[
                            {
                                "id": "0d1c7b2e-4a3f-4e8b-9c1d-7f6e5a4b3c2d",
                                "reference": "SCB-0d1c7b2e-A",
                                "title": "Advanced Rust concurrency patterns",
                                "status": "PENDING_REVIEW",
                                "payload": {
                                    "title": "Advanced Rust concurrency patterns",
                                    "description": (
                                        "Deep dive into tokio, async/await, and "
                                        "lock-free data structures for Rust devs."
                                    ),
                                    "target_audience": "intermediate",
                                },
                                "developer_id": "a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
                                "developer_email": "ada@rustdev.io",
                                "payout_bypass": False,
                                "demand_score": 87,
                                "estimated_monthly_earnings": "4200.00",
                                "rejection_reason": None,
                                "rejection_note": "",
                                "queued_at": "2026-08-20T10:15:33.102Z",
                                "decided_at": None,
                                "decided_by_email": None,
                                "resulting_course": None,
                                "created_datetime": "2026-08-20T10:15:33.102Z",
                                "updated_datetime": "2026-08-20T10:15:33.102Z",
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
        summary="Retrieve a submission",
        description=(
            "Returns the full admin detail for a single submission, "
            "including the verbatim Endpoint 1 payload, rejection "
            "metadata, payout bypass status, and demand signals. This "
            "gives the superadmin every field needed to make an informed "
            "approval or rejection decision.\n\n"
            "Call this endpoint when you need the complete picture of a "
            "submission before approving, rejecting, or updating its "
            "signal metadata.\n\n"
            "**Auth:** Superadmin role required.\n\n"
            "**Prerequisites:** The submission must exist and be "
            "accessible.\n\n"
            "**Important:** None."
        ),
        responses={
            200: OpenApiResponse(
                response=AdminSubmissionSerializer,
                description="Full admin detail for the requested submission.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "id": "0d1c7b2e-4a3f-4e8b-9c1d-7f6e5a4b3c2d",
                            "reference": "SCB-0d1c7b2e-A",
                            "title": "Advanced Rust concurrency patterns",
                            "status": "PENDING_REVIEW",
                            "payload": {
                                "title": "Advanced Rust concurrency patterns",
                                "description": (
                                    "Deep dive into tokio, async/await, and "
                                    "lock-free data structures for Rust devs."
                                ),
                                "target_audience": "intermediate",
                            },
                            "developer_id": "a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
                            "developer_email": "ada@rustdev.io",
                            "payout_bypass": False,
                            "demand_score": 87,
                            "estimated_monthly_earnings": "4200.00",
                            "rejection_reason": None,
                            "rejection_note": "",
                            "queued_at": "2026-08-20T10:15:33.102Z",
                            "decided_at": None,
                            "decided_by_email": None,
                            "resulting_course": None,
                            "created_datetime": "2026-08-20T10:15:33.102Z",
                            "updated_datetime": "2026-08-20T10:15:33.102Z",
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
        summary="Approve a submission",
        description=(
            "Marks a submission as accepted and triggers the downstream "
            "payout pipeline. This works from any pipeline state — "
            "re-approving a previously rejected idea clears its rejection "
            "metadata and re-links any course that was produced for it. "
            "Wallet credit follows the developer's plan unless the payout "
            "bypass flag is set.\n\n"
            "Call this endpoint after reviewing a submission's detail and "
            "confirming it meets quality standards. Once approved the "
            "developer is notified immediately and the payout flow begins.\n\n"
            "**Auth:** Superadmin role required.\n\n"
            "**Prerequisites:** The submission must exist and be "
            "accessible.\n\n"
            "**Important:** Approval fires a SUBMISSION_APPROVED webhook "
            "to the developer immediately. Wallet credit follows the "
            "developer's plan unless payout_bypass is set on the "
            "submission. Re-approving a previously rejected submission "
            "clears all rejection metadata and re-links any course that "
            "was produced from the original approval."
        ),
        request=SubmissionDecisionSerializer,
        responses={
            200: OpenApiResponse(
                response=SubmissionDecisionResponseSerializer,
                description="Confirmation of the approval with updated submission.",
                examples=[
                    OpenApiExample(
                        name="Approved idea",
                        value={
                            "detail": "Submission SCB-0d1c7b2e-A approved.",
                            "submission": {
                                "id": "0d1c7b2e-4a3f-4e8b-9c1d-7f6e5a4b3c2d",
                                "reference": "SCB-0d1c7b2e-A",
                                "title": "Advanced Rust concurrency patterns",
                                "status": "APPROVED",
                                "payload": {
                                    "title": "Advanced Rust concurrency patterns",
                                    "description": (
                                        "Deep dive into tokio, async/await, and "
                                        "lock-free data structures for Rust devs."
                                    ),
                                    "target_audience": "intermediate",
                                },
                                "developer_id": "a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
                                "developer_email": "ada@rustdev.io",
                                "payout_bypass": False,
                                "demand_score": 87,
                                "estimated_monthly_earnings": "4200.00",
                                "rejection_reason": None,
                                "rejection_note": "",
                                "queued_at": "2026-08-20T10:15:33.102Z",
                                "decided_at": "2026-08-25T14:30:00.000Z",
                                "decided_by_email": "admin@feexeet.com",
                                "resulting_course": None,
                                "created_datetime": "2026-08-20T10:15:33.102Z",
                                "updated_datetime": "2026-08-25T14:30:00.000Z",
                            },
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
        return self._decide(request, approve=True)

    @extend_schema(
        summary="Reject a submission",
        description=(
            "Marks a submission as rejected using a structured rejection "
            "reason label. This works from any state including APPROVED "
            "— if a course was already produced for the idea it is "
            "unpublished and parked for review rather than deleted, so a "
            "later re-approval can relink it without data loss.\n\n"
            "Call this endpoint after reviewing a submission and "
            "determining it does not meet quality standards. The rejection "
            "reason is required so the developer receives actionable "
            "feedback.\n\n"
            "**Auth:** Superadmin role required.\n\n"
            "**Prerequisites:** The submission must exist and be "
            "accessible. The `rejection_reason` label must match an "
            "active SubmissionRejectionReason record.\n\n"
            "**Important:** Fires a SUBMISSION_REJECTED webhook to the "
            "developer immediately. If a course was already produced from "
            "a previous approval, it is unpublished and parked — never "
            "deleted — so a later re-approval can relink it. This action "
            "works from any pipeline state including APPROVED."
        ),
        request=SubmissionDecisionSerializer,
        responses={
            200: OpenApiResponse(
                response=SubmissionDecisionResponseSerializer,
                description="Confirmation of the rejection with updated submission.",
                examples=[
                    OpenApiExample(
                        name="Reject with reason",
                        value={
                            "detail": "Submission SCB-0d1c7b2e-A rejected.",
                            "submission": {
                                "id": "0d1c7b2e-4a3f-4e8b-9c1d-7f6e5a4b3c2d",
                                "reference": "SCB-0d1c7b2e-A",
                                "title": "Advanced Rust concurrency patterns",
                                "status": "REJECTED",
                                "payload": {
                                    "title": "Advanced Rust concurrency patterns",
                                    "description": (
                                        "Deep dive into tokio, async/await, and "
                                        "lock-free data structures for Rust devs."
                                    ),
                                    "target_audience": "intermediate",
                                },
                                "developer_id": "a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
                                "developer_email": "ada@rustdev.io",
                                "payout_bypass": False,
                                "demand_score": 87,
                                "estimated_monthly_earnings": "4200.00",
                                "rejection_reason": "Duplicate of existing catalog",
                                "rejection_note": (
                                    "Covered by the live Rust course."
                                ),
                                "queued_at": "2026-08-20T10:15:33.102Z",
                                "decided_at": "2026-08-25T14:30:00.000Z",
                                "decided_by_email": "admin@feexeet.com",
                                "resulting_course": None,
                                "created_datetime": "2026-08-20T10:15:33.102Z",
                                "updated_datetime": "2026-08-25T14:30:00.000Z",
                            },
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
    def reject(self, request, id=None):
        return self._decide(request, approve=False)

    def _decide(self, request, *, approve: bool) -> Response:
        submission = self.get_object()
        serializer = SubmissionDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rejection_reason = None
        if not approve:
            label = serializer.validated_data.get("rejection_reason")
            if not label:
                raise exceptions.ValidationError(
                    {"rejection_reason": ["A rejection reason label is required to reject."]}
                )
            rejection_reason = get_object_or_404(
                SubmissionRejectionReason, label=label, is_active=True
            )
        updated = submission_admin_service.decide_submission(
            actor=request.user,
            submission=submission,
            approve=approve,
            rejection_reason=rejection_reason,
            rejection_note=serializer.validated_data.get("rejection_note", ""),
        )
        return Response({
            "detail": f"Submission {updated.public_reference} "
            f"{'approved' if approve else 'rejected'}.",
            "submission": AdminSubmissionSerializer(updated).data,
        })

    @extend_schema(
        summary="Set recommendation signals",
        description=(
            "Records the admin-entered demand score and optional estimated "
            "monthly earnings that prioritise the Recommendations queue "
            "for this submission. This is purely advisory metadata that "
            "the recommendation engine reads — it fires no webhook and "
            "has no effect on payout or approval status.\n\n"
            "Call this endpoint after reviewing market data for a "
            "submission. Demand signals are used by the Recommendations "
            "engine to decide which approved ideas get surfaced first in "
            "the catalog.\n\n"
            "**Auth:** Superadmin role required.\n\n"
            "**Prerequisites:** The submission must exist and be "
            "accessible.\n\n"
            "**Important:** This endpoint only sets advisory metadata — "
            "it fires no webhook and has no effect on the payout pipeline "
            "or approval status. The demand_score is an integer from 0 to "
            "100. estimated_monthly_earnings is optional and can be "
            "omitted or set to null to clear a previous value."
        ),
        request=DemandSignalsSerializer,
        responses={
            200: OpenApiResponse(
                response=AdminSubmissionSerializer,
                description="The submission with updated demand signals.",
                examples=[
                    OpenApiExample(
                        name="High-demand idea with earnings estimate",
                        value={
                            "id": "0d1c7b2e-4a3f-4e8b-9c1d-7f6e5a4b3c2d",
                            "reference": "SCB-0d1c7b2e-A",
                            "title": "Advanced Rust concurrency patterns",
                            "status": "PENDING_REVIEW",
                            "payload": {
                                "title": "Advanced Rust concurrency patterns",
                                "description": (
                                    "Deep dive into tokio, async/await, and "
                                    "lock-free data structures for Rust devs."
                                ),
                                "target_audience": "intermediate",
                            },
                            "developer_id": "a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
                            "developer_email": "ada@rustdev.io",
                            "payout_bypass": False,
                            "demand_score": 87,
                            "estimated_monthly_earnings": "4200.00",
                            "rejection_reason": None,
                            "rejection_note": "",
                            "queued_at": "2026-08-20T10:15:33.102Z",
                            "decided_at": None,
                            "decided_by_email": None,
                            "resulting_course": None,
                            "created_datetime": "2026-08-20T10:15:33.102Z",
                            "updated_datetime": "2026-08-25T14:30:00.000Z",
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
    def signals(self, request, id=None):
        submission = self.get_object()
        serializer = DemandSignalsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = submission_admin_service.set_demand_signals(
            actor=request.user,
            submission=submission,
            demand_score=serializer.validated_data["demand_score"],
            estimated_monthly_earnings=serializer.validated_data.get(
                "estimated_monthly_earnings"
            ),
        )
        return Response(AdminSubmissionSerializer(updated).data)

    @extend_schema(
        summary="Toggle payout bypass",
        description=(
            "Toggles the payout bypass flag on a specific submission, "
            "marking it as no-payout or clearing the mark. The developer "
            "receives a real-time event when this changes so they "
            "understand why a submission will not earn credit.\n\n"
            "Call this endpoint when a submission should be excluded from "
            "the payout pipeline, for example when it was submitted as a "
            "test or violates the developer agreement, without rejecting "
            "the idea itself.\n\n"
            "**Auth:** Superadmin role required.\n\n"
            "**Prerequisites:** The submission must exist and be "
            "accessible.\n\n"
            "**Important:** Fires a SUBMISSION_PAYOUT_BYPASS_UPDATED "
            "webhook to the developer immediately. Rejects silently if "
            "the bypass is already in the requested state (returns the "
            "current submission without changing it). This is a soft "
            "toggle — it has no effect on approval status or rejection "
            "metadata."
        ),
        request=PayoutBypassSerializer,
        responses={
            200: OpenApiResponse(
                response=AdminSubmissionSerializer,
                description="The submission with updated payout bypass status.",
                examples=[
                    OpenApiExample(
                        name="Mark no-payout",
                        value={
                            "id": "0d1c7b2e-4a3f-4e8b-9c1d-7f6e5a4b3c2d",
                            "reference": "SCB-0d1c7b2e-A",
                            "title": "Advanced Rust concurrency patterns",
                            "status": "PENDING_REVIEW",
                            "payload": {
                                "title": "Advanced Rust concurrency patterns",
                                "description": (
                                    "Deep dive into tokio, async/await, and "
                                    "lock-free data structures for Rust devs."
                                ),
                                "target_audience": "intermediate",
                            },
                            "developer_id": "a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
                            "developer_email": "ada@rustdev.io",
                            "payout_bypass": True,
                            "demand_score": 87,
                            "estimated_monthly_earnings": "4200.00",
                            "rejection_reason": None,
                            "rejection_note": "",
                            "queued_at": "2026-08-20T10:15:33.102Z",
                            "decided_at": None,
                            "decided_by_email": None,
                            "resulting_course": None,
                            "created_datetime": "2026-08-20T10:15:33.102Z",
                            "updated_datetime": "2026-08-25T14:30:00.000Z",
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
    def payout_bypass(self, request, id=None):
        submission = self.get_object()
        serializer = PayoutBypassSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = submission_admin_service.set_payout_bypass(
            actor=request.user,
            submission=submission,
            bypass=serializer.validated_data["payout_bypass"],
        )
        return Response(AdminSubmissionSerializer(updated).data)
