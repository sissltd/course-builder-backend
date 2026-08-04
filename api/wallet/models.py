import uuid
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from api.wallet.enums import (
    PayoutAccountType,
    TransactionStatus,
    TransactionType,
    WithdrawalRequestStatus,
)
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


def _generate_reference() -> str:
    """A short, unique, user-facing reference shown/copied on transaction
    detail screens - independent of the internal UUID primary key."""

    return f"TXN-{uuid.uuid4().hex[:12].upper()}"


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


class PayoutAccount(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A bank or mobile-money account a creator can withdraw earnings to.

    A creator can hold multiple (Settings -> Payment shows Local + Mobile
    Money side by side); is_default controls which one is preselected on the
    withdrawal screen.
    """

    user = models.ForeignKey(
        "users.User",
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="payout_accounts",
        help_text=_("User who owns this payout account."),
    )
    account_type = models.CharField(
        verbose_name=_("Account Type"),
        max_length=15,
        choices=PayoutAccountType.choices,
        help_text=_("Whether this is a local bank account or a mobile money account."),
    )
    provider_name = models.CharField(
        verbose_name=_("Provider Name"),
        max_length=100,
        help_text=_("Bank name (LOCAL) or mobile money provider (MOBILE_MONEY)."),
    )
    account_number = models.CharField(
        verbose_name=_("Account Number"),
        max_length=34,
        help_text=_("Bank account number or mobile money number."),
    )
    account_name = models.CharField(
        verbose_name=_("Account Name"),
        max_length=150,
        help_text=_("Name on the account, as entered when adding it."),
    )
    is_default = models.BooleanField(
        verbose_name=_("Is Default"),
        default=False,
        help_text=_("Preselected payout account on the withdrawal screen."),
    )

    class Meta:
        verbose_name = _("Payout Account")
        verbose_name_plural = _("Payout Accounts")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(fields=["user"], name="payout_acct_user_idx"),
        ]

    def __str__(self):
        """Summarize the payout account for admin/debugging readability."""

        return f"{self.account_type} {self.account_number} ({self.user_id})"


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
    payout_account = models.ForeignKey(
        "wallet.PayoutAccount",
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
        unique=True,
        default=_generate_reference,
        help_text=_("Unique reference shown to the user, e.g. on a receipt."),
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
        max_length=34,
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

    class Meta:
        verbose_name = _("Transaction")
        verbose_name_plural = _("Transactions")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(
                fields=["wallet", "-created_datetime"], name="txn_wallet_dt_idx"
            ),
            models.Index(fields=["status"], name="txn_status_idx"),
        ]

    def __str__(self):
        """Summarize the transaction for admin/debugging readability."""

        return f"{self.type} {self.amount} ({self.status})"


class WithdrawalRequest(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """The OTP-gated step between "Request withdrawal" and the resulting
    Transaction.

    Created by wallet_service.request_withdrawal (validates amount/balance/
    KYC, emails an OTP - see authentication.token_service.issue_numeric_code
    with TokenPurpose.WITHDRAWAL_CONFIRMATION - but does not touch the wallet
    balance yet). wallet_service.confirm_withdrawal verifies the OTP, then
    atomically decrements the balance and creates the Transaction, linking it
    back here via `transaction`.
    """

    user = models.ForeignKey(
        "users.User",
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="withdrawal_requests",
        help_text=_("User requesting the withdrawal."),
    )
    wallet = models.ForeignKey(
        "wallet.Wallet",
        verbose_name=_("Wallet"),
        on_delete=models.CASCADE,
        related_name="withdrawal_requests",
        help_text=_("Wallet the withdrawal will be debited from."),
    )
    payout_account = models.ForeignKey(
        "wallet.PayoutAccount",
        verbose_name=_("Payout Account"),
        on_delete=models.CASCADE,
        related_name="withdrawal_requests",
        help_text=_("Payout account the funds will be sent to."),
    )
    amount = models.DecimalField(
        verbose_name=_("Amount"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text=_("Amount requested for withdrawal."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=25,
        choices=WithdrawalRequestStatus.choices,
        default=WithdrawalRequestStatus.PENDING_CONFIRMATION,
        help_text=_("State of this withdrawal request."),
    )
    transaction = models.OneToOneField(
        "wallet.Transaction",
        verbose_name=_("Transaction"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="withdrawal_request",
        help_text=_("Transaction created once this request is confirmed."),
    )
    confirmed_at = models.DateTimeField(
        verbose_name=_("Confirmed At"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("Withdrawal Request")
        verbose_name_plural = _("Withdrawal Requests")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(fields=["user", "-created_datetime"], name="wdr_user_dt_idx"),
        ]

    def __str__(self):
        """Summarize the withdrawal request for admin/debugging readability."""

        return f"WithdrawalRequest({self.user_id}, {self.amount}, {self.status})"
