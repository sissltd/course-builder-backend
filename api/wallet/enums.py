from django.db import models


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


class PayoutAccountType(models.TextChoices):
    """How a creator's payout account receives funds."""

    LOCAL = "LOCAL", "Local Account"
    MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"


class WithdrawalRequestStatus(models.TextChoices):
    """State of a withdrawal between "Request withdrawal" and OTP confirmation."""

    PENDING_CONFIRMATION = "PENDING_CONFIRMATION", "Pending Confirmation"
    CONFIRMED = "CONFIRMED", "Confirmed"
    EXPIRED = "EXPIRED", "Expired"
