from decimal import Decimal

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import serializers, status
from rest_framework.views import APIView

from api.authorization import codenames
from api.authorization.permissions import IsStrongMFASession, Perm
from api.wallet.enums import AdjustmentDirection
from api.wallet.models import Wallet, WalletAdjustment
from api.wallet.services import wallet_adjustment_service
from includes.helpers.pagination import PageNumberAPIPagination
from includes.spectacular.responses import (
    STANDARD_ERROR_RESPONSES,
    ErrorEnvelopeSerializer,
    inline_success_response,
)
from shared.response.success import custom_success_response

TAG = "Admin — Wallets"

_ADJUSTMENT_EXAMPLE = {
    "id": "2f1e0d9c-8b7a-4c6d-9e5f-4a3b2c1d0e9f",
    "wallet": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
    "user": {"id": "9f8e7d6c-5b4a-4321-8765-0fedcba98765", "email": "ada@example.com"},
    "direction": "CREDIT",
    "amount": "5000.00",
    "reason": "Missed payout for 'Intro to Python'",
    "reference": "ADJ-4c1f0b2a",
    "created_by_email": "superadmin@example.com",
    "created_datetime": "2026-09-17T10:00:00Z",
}


class AdjustmentUserSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="Creator's user id.")
    email = serializers.EmailField(help_text="Creator's email.")


class WalletAdjustmentSerializer(serializers.ModelSerializer):
    user = AdjustmentUserSerializer(help_text="The creator whose wallet was adjusted.")
    created_by_email = serializers.EmailField(
        source="created_by.email",
        allow_null=True,
        default=None,
        help_text="The administrator who made the adjustment.",
    )

    class Meta:
        model = WalletAdjustment
        fields = [
            "id",
            "wallet",
            "user",
            "direction",
            "amount",
            "reason",
            "reference",
            "created_by_email",
            "created_datetime",
        ]
        read_only_fields = fields
        extra_kwargs = {
            "id": {"help_text": "Adjustment id."},
            "wallet": {"help_text": "Wallet id."},
            "direction": {
                "help_text": "CREDIT adds to the wallet; DEBIT takes from it."
            },
            "amount": {"help_text": "Amount moved, always positive."},
            "reason": {"help_text": "Why the adjustment was made."},
            "reference": {
                "help_text": "Ledger reference shared by both transaction entries."
            },
            "created_datetime": {"help_text": "When it was made."},
        }


class WalletAdjustmentCreateSerializer(serializers.Serializer):
    direction = serializers.ChoiceField(
        choices=AdjustmentDirection.choices, help_text="CREDIT or DEBIT."
    )
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="Positive amount to move.",
    )
    reason = serializers.CharField(
        max_length=500, help_text="Shown to the creator and audited."
    )
    idempotency_key = serializers.CharField(
        max_length=64,
        help_text=(
            "Unique per intended adjustment. Retrying with the same key returns "
            "the original instead of moving money twice."
        ),
    )


class WalletAdjustmentCreateView(APIView):
    permission_classes = [Perm(codenames.CREATORS_ISSUE_REFUND), IsStrongMFASession]
    serializer_class = WalletAdjustmentSerializer  # schema generation only

    @extend_schema(
        summary="Adjust a creator's wallet",
        description=(
            "Credits or debits a creator's wallet with a reason - the Issue "
            "Refund action. Double-entry against the platform's adjustments "
            "ledger account.\n\n"
            "**Auth:** The `creators.issue_refund` permission (Issue Refund) — "
            "Super Admin by default — and a session verified with multi-factor "
            "authentication where MFA is enforced.\n\n"
            "**Prerequisites:** The wallet must belong to a Course Creator or "
            "Writer other than the caller.\n\n"
            "**Important:** A debit larger than the balance is 400; a wallet "
            "never goes negative. Retrying with the same `idempotency_key` and "
            "payload returns the original adjustment with 200; the same key "
            "with a different payload is 409. The creator is notified."
        ),
        tags=[TAG],
        request=WalletAdjustmentCreateSerializer,
        examples=[
            OpenApiExample(
                name="Credit",
                request_only=True,
                value={
                    "direction": "CREDIT",
                    "amount": "5000.00",
                    "reason": "Missed payout",
                    "idempotency_key": "4c1f0b2a",
                },
            )
        ],
        responses={
            201: inline_success_response(
                description="The adjustment.",
                examples=[
                    OpenApiExample(
                        name="Created",
                        value={
                            "success": True,
                            "status": 201,
                            "message": "Wallet adjusted.",
                            "data": _ADJUSTMENT_EXAMPLE,
                        },
                    )
                ],
            ),
            200: inline_success_response(
                description="A retry with an idempotency key already used for this exact adjustment.",
                examples=[
                    OpenApiExample(
                        name="Replayed",
                        value={
                            "success": True,
                            "status": 200,
                            "message": "Adjustment already made.",
                            "data": _ADJUSTMENT_EXAMPLE,
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            409: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description="The idempotency key was used for a different adjustment.",
                examples=[
                    OpenApiExample(
                        name="Conflict",
                        value={
                            "errors": [
                                {
                                    "type": "client_error",
                                    "code": "adjustment_conflict",
                                    "message": "This idempotency key was already used for a different adjustment.",
                                    "field_name": None,
                                }
                            ]
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request, wallet_id):
        wallet = get_object_or_404(Wallet.objects.select_related("user"), pk=wallet_id)
        serializer = WalletAdjustmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        adjustment, created = wallet_adjustment_service.adjust_wallet(
            actor=request.user,
            wallet=wallet,
            request=request,
            **serializer.validated_data,
        )
        return custom_success_response(
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            message="Wallet adjusted." if created else "Adjustment already made.",
            data=WalletAdjustmentSerializer(adjustment).data,
        )


class WalletAdjustmentListView(APIView):
    permission_classes = [
        Perm(codenames.CREATORS_VIEW_WALLET, codenames.CREATORS_ISSUE_REFUND)
    ]
    pagination_class = PageNumberAPIPagination
    serializer_class = WalletAdjustmentSerializer  # schema generation only

    @extend_schema(
        summary="List wallet adjustments",
        description=(
            "Every admin wallet adjustment, newest first.\n\n"
            "**Auth:** `creators.view_wallet` or `creators.issue_refund` — Admin "
            "and Super Admin by default.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Paginated under `data.results` / `data.paginator`."
        ),
        tags=[TAG],
        parameters=[
            OpenApiParameter(
                "user_id",
                str,
                required=False,
                description="Only this creator's adjustments.",
            ),
            OpenApiParameter(
                "direction",
                str,
                required=False,
                enum=AdjustmentDirection.values,
                description="CREDIT or DEBIT.",
            ),
            OpenApiParameter("page", int, required=False, description="Page number."),
            OpenApiParameter("size", int, required=False, description="Rows per page."),
        ],
        responses={
            200: inline_success_response(
                description="A page of adjustments.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "status": True,
                            "message": "Successfully retrieved data",
                            "data": {
                                "paginator": {
                                    "count": 1,
                                    "page": 1,
                                    "page_size": 10,
                                    "total_pages": 1,
                                    "next": None,
                                    "next_page_number": None,
                                    "previous": None,
                                    "previous_page_number": None,
                                },
                                "results": [_ADJUSTMENT_EXAMPLE],
                            },
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        query = _AdjustmentQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        adjustments = wallet_adjustment_service.list_adjustments(
            actor=request.user, **query.validated_data
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(adjustments, request, self)
        return paginator.get_paginated_response(
            WalletAdjustmentSerializer(page, many=True).data
        )


class _AdjustmentQuerySerializer(serializers.Serializer):
    user_id = serializers.UUIDField(required=False, help_text="Filter by creator.")
    direction = serializers.ChoiceField(
        choices=AdjustmentDirection.choices,
        required=False,
        help_text="Filter by direction.",
    )
