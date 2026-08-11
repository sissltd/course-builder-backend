from decimal import Decimal

from django.contrib.contenttypes.fields import GenericRelation
from django.db import models

from core.mixins import (
    DateHistoryModelMixin,
    SoftDeleteModelMixin,
    UUIDPrimaryKeyModelMixin,
)
from core.models import TransferOutboxEvent

# from shared.models.outbox_models import TransferOutboxEvent


class InternalAccount(
    UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, SoftDeleteModelMixin, models.Model
):
    """Internal account for internal ledger transactions. This model represents a ledger account that is used to temporarily hold funds or transactions before they are allocated to their final destination. It is designed to facilitate the management of internal financial operations within the system.

    - Transit Account: This account acts as a temporary holding place for funds that are in transit between different accounts or systems. It ensures that transactions are properly tracked and managed during the transfer process.

    - Paystack Account: This account is used for transactions related to Paystack, a payment processing platform. It allows for the management of funds that are processed through Paystack, ensuring that they are properly accounted for within the internal ledger system.
    """

    class Currency(models.TextChoices):
        NGN = "NGN", "NGN"

    name = models.CharField(
        max_length=50,
        unique=True,
        help_text="Name of the internal ledger (e.g., 'Feexeet Subscription Revenue', 'Labour Fees Ledger', etc.)",
    )
    code_name = models.CharField(
        max_length=50,
        unique=True,
        help_text="A short unique code name for the internal ledger (e.g., 'subscription', 'labour', etc.) that can be used in code to reference the ledger without relying on the human-readable name. This allows for safer code references that won't break if the 'name' field is updated for clarity or rebranding purposes.",
    )
    description = models.TextField(blank=True, null=True)

    balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    last_deposit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    last_withdrawal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    last_withdrawal_timestamp = models.DateTimeField(
        help_text="Timestamp of last withdrawal", null=True, blank=True
    )
    last_deposit_timestamp = models.DateTimeField(
        help_text="Timestamp of last deposit", null=True, blank=True
    )
    currency = models.CharField(max_length=10, choices=Currency.choices)
    transactions = GenericRelation(
        "payments.Transaction",
        content_type_field="wallet_type",
        object_id_field="wallet_id",
    )
    entries = GenericRelation(
        TransferOutboxEvent,
        content_type_field="wallet_type",
        object_id_field="wallet_id",
    )

    class Meta:
        db_table = "internal_account"

    def __str__(self):
        return str(self.name)
