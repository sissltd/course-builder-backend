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
