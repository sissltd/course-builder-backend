from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from api.wallet.enums import TransactionStatus, TransactionType
from includes.helpers import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class Wallet(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A creator's in-platform financial account (SCCS PRD Section 7.2).

    Wallets are lazily created on first access via wallet_service.get_or_create_wallet,
    not via a post_save signal on User, so no wallet row is provisioned at signup.
    """

    user = models.OneToOneField(
        "users.User",
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="wallet",
        help_text=_("User who owns this wallet."),
    )
    balance = models.DecimalField(
        verbose_name=_("Balance"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Current wallet balance. Must never go negative."),
    )
    currency = models.CharField(
        verbose_name=_("Currency"),
        max_length=3,
        default="USD",
        help_text=_("ISO 4217 currency code."),
    )

    class Meta:
        verbose_name = _("Wallet")
        verbose_name_plural = _("Wallets")
        indexes = [
            models.Index(fields=["user"], name="wallet_user_idx"),
        ]

    def __str__(self):
        """Use the owning user's email as the human-readable label."""

        return f"Wallet({self.user_id})"


class Transaction(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """An immutable record of a wallet balance change."""

    wallet = models.ForeignKey(
        "wallet.Wallet",
        verbose_name=_("Wallet"),
        on_delete=models.CASCADE,
        related_name="transactions",
        help_text=_("Wallet this transaction belongs to."),
    )
    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wallet_transactions",
        help_text=_("Course this transaction is associated with, if any."),
    )
    amount = models.DecimalField(
        verbose_name=_("Amount"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text=_("Absolute transaction amount, always positive."),
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

    class Meta:
        verbose_name = _("Transaction")
        verbose_name_plural = _("Transactions")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(fields=["wallet", "-created_datetime"], name="txn_wallet_dt_idx"),
            models.Index(fields=["status"], name="txn_status_idx"),
        ]

    def __str__(self):
        """Summarize the transaction for admin/debugging readability."""

        return f"{self.type} {self.amount} ({self.status})"
