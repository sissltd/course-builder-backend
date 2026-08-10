import uuid
from decimal import Decimal
from typing import ClassVar

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import DateHistoryModelMixin, SoftDeleteModelMixin, UUIDPrimaryKeyModelMixin


def generate_reference() -> str:
    """A short, unique, user-facing reference shown/copied on transaction
    detail screens - independent of the internal UUID primary key."""

    return f"TXN-{uuid.uuid4().hex[:12].upper()}"


class Transaction(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, SoftDeleteModelMixin, models.Model):
    """An immutable record of a wallet balance change."""
    
    class TransactionType(models.TextChoices):
        """Direction of a wallet transaction."""

        CREDIT = "CREDIT", "Credit"
        DEBIT = "DEBIT", "Debit"


    class TransactionStatus(models.TextChoices):
        """Settlement status of a wallet transaction.

        COMPLETED is used for immediate in-platform credits (e.g. course approval).
        PENDING is used for withdrawal requests awaiting manual/future payment
        gateway settlement (Paystack/Flutterwave integration is deferred).
        """

        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wallet_transactions",
        help_text=_("Course this transaction is associated with, if any."),
    )
    payout_account = models.ForeignKey(
        "payments.BankAccount",
        verbose_name=_("Payout Account"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        help_text=_("Payout account this withdrawal was sent to, if any."),
    )
    reference = models.CharField(
        verbose_name=_("Reference"),
        max_length=32,
        help_text=_("This is non-unique, so the same reference can be associated with related transactions (e.g. a debit and credit pair)."),
    )
    amount = models.DecimalField(
        verbose_name=_("Amount"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text=_("Absolute transaction amount, always positive."),
    )
    fee = models.DecimalField(
        verbose_name=_("Fee"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Fee charged for this transaction, if any."),
    )
    type = models.CharField(
        verbose_name=_("Type"),
        max_length=10,
        choices=TransactionType.choices,
        help_text=_("Whether this transaction credits or debits the wallet."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=TransactionStatus.choices,
        default=TransactionStatus.PENDING,
        help_text=_("Settlement status of the transaction."),
    )
    description = models.CharField(
        verbose_name=_("Description"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Human-readable description of the transaction."),
    )
    recipient_account_name = models.CharField(
        verbose_name=_("Recipient Account Name"),
        max_length=150,
        blank=True,
        default="",
        help_text=_(
            "Snapshot of the payout account's holder name at transaction time, "
            "so the receipt stays accurate if the account is later edited/removed."
        ),
    )
    recipient_account_number = models.CharField(
        verbose_name=_("Recipient Account Number"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Snapshot of the payout account number at transaction time."),
    )
    recipient_provider_name = models.CharField(
        verbose_name=_("Recipient Provider Name"),
        max_length=100,
        blank=True,
        default="",
        help_text=_("Snapshot of the bank/mobile-money provider at transaction time."),
    )
    wallet_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )  # May be Wallet, InternalAccount, etc. depending on the transaction context.
    wallet_id = models.UUIDField(null=True, blank=True)
    wallet = GenericForeignKey("wallet_type", "wallet_id")

    class Meta:
        verbose_name = _("Transaction")
        verbose_name_plural = _("Transactions")
        ordering = ["-created_datetime"]
        indexes: ClassVar = [
            models.Index(fields=["wallet_type", "wallet_id", "-created_datetime"], name="txn_wallet_cdt_idx"),
            models.Index(fields=["status"], name="txn_status_idx2"),
        ]

    def __str__(self):
        """Summarize the transaction for admin/debugging readability."""

        return f"{self.type} {self.amount} ({self.status})"
