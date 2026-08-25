from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import exceptions, status, viewsets
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


@extend_schema(tags=["Admin — MIE Submissions"])
class MieSubmissionAdminViewSet(viewsets.ReadOnlyModelViewSet):
    """Superadmin queue over every developer's submissions.

    This is the counterpart of the developer's own /queue/ surface:
    identical data shape plus admin fields (raw payload, demand signals,
    decision metadata), and the full filter set including by-developer -
    which the dev route deliberately does not expose.

    Decisions are reversible: approve and reject work from any state,
    every flip fires its webhook immediately.
    """

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
            "Every submission from every developer, newest first. Filters: "
            "?status=, ?payout_bypass=, ?developer=<uuid>, ?email= "
            "(exact developer email), ?created_after=/?created_before= "
            "(ISO-8601), ?search= (title or developer email substring)."
        ),
        responses={status.HTTP_200_OK: AdminSubmissionSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Retrieve a submission",
        description=(
            "Full admin detail for one submission, including the verbatim "
            "Endpoint 1 payload."
        ),
        responses={status.HTTP_200_OK: AdminSubmissionSerializer},
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        summary="Approve a submission",
        description=(
            "Mark the idea accepted. Works from any state - re-approving "
            "a previously rejected idea clears its rejection metadata and "
            "re-links any produced course. Fires SUBMISSION_APPROVED to "
            "the developer immediately. Wallet credit follows the "
            "developer's plan unless payout_bypass is set."
        ),
        request=SubmissionDecisionSerializer,
        responses={
            status.HTTP_200_OK: SubmissionDecisionResponseSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(
                description="Unknown rejection_reason label."
            ),
        },
    )
    @action(detail=True, methods=["post"])
    def approve(self, request, id=None):
        return self._decide(request, approve=True)

    @extend_schema(
        summary="Reject a submission",
        description=(
            "Mark the idea rejected; requires a rejection reason label. "
            "Works from any state including APPROVED - if a course was "
            "already produced it is unpublished and parked for review, "
            "never deleted, so a later re-approval relinks it. Fires "
            "SUBMISSION_REJECTED immediately."
        ),
        request=SubmissionDecisionSerializer,
        responses={status.HTTP_200_OK: SubmissionDecisionResponseSerializer},
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
        return Response(
            {
                "detail": f"Submission {updated.public_reference} "
                f"{'approved' if approve else 'rejected'}.",
                "submission": AdminSubmissionSerializer(updated).data,
            }
        )

    @extend_schema(
        summary="Set recommendation signals",
        description=(
            "Record the admin-entered demand score (0-100) and optional "
            "estimated monthly earnings that prioritise the Recommendations "
            "queue. Purely advisory metadata; fires no webhook."
        ),
        request=DemandSignalsSerializer,
        responses={status.HTTP_200_OK: AdminSubmissionSerializer},
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
            "Mark this specific idea as no-payout (or clear the mark). "
            "The developer receives SUBMISSION_PAYOUT_BYPASS_UPDATED on "
            "the wire immediately. Rejects silently-identical toggles."
        ),
        request=PayoutBypassSerializer,
        responses={
            status.HTTP_200_OK: AdminSubmissionSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(
                description="Payout bypass already in the requested state."
            ),
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
