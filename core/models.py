# models.py

from typing import ClassVar

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from api.platform.enums import PaymentProcessors
from api.users.enums import KYCDocumentType

from .mixins import (
    DateHistoryModelMixin,
    SoftDeleteModelMixin,
    UUIDPrimaryKeyModelMixin,
)

User = get_user_model()


class KYCLivenessType(models.TextChoices):
    """Including Liveness verification."""

    LIVENESS = "LIVENESS", "Liveness Verification"


class KYCOutboxEvent(UUIDPrimaryKeyModelMixin, SoftDeleteModelMixin, DateHistoryModelMixin, models.Model):
    """Stores outbox events waiting to be dispatched safely to an event bus."""

    event_type = models.CharField(max_length=255, choices=[*KYCDocumentType.choices, *KYCLivenessType.choices])
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    kyc_request = models.OneToOneField(
        "users.KYCVerification",
        on_delete=models.CASCADE,
        related_name="kyc_outbox_event",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "kyc_outbox_events"
        ordering = ["created_datetime"]


class WebhookEvent(UUIDPrimaryKeyModelMixin, SoftDeleteModelMixin, DateHistoryModelMixin, models.Model):
    """Outbox event for payment webhooks."""
    STATUS_CHOICES: ClassVar = [
        ("PENDING", "Pending"),
        ("PROCESSING", "Processing"),
        ("PROCESSED", "Processed"),
        ("FAILED", "Failed"),
    ]

    # Paystack sends a unique ID per event (e.g., event_id or data.id)
    event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100)  # e.g., 'charge.success'
    payload = models.JSONField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    error_message = models.TextField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    provider = models.CharField(
        max_length=200, choices=PaymentProcessors.choices, default=PaymentProcessors.FLUTTERWAVE
    )

    def __str__(self):
        return f"{self.event_type} - {self.event_id} ({self.status})"

    class Meta:
        db_table = "webhook_events"
        ordering = ["created_datetime"]


class TransferOutboxEvent(
    UUIDPrimaryKeyModelMixin, SoftDeleteModelMixin, DateHistoryModelMixin, models.Model
):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending API Call"
        PROCESSING = "PROCESSING", "API Call In Progress"
        SUBMITTED = (
            "SUBMITTED",
            "Sent to Processor",
        )  # Paystack/Flutterwave accepted it, now awaiting webhook
        FAILED = "FAILED", "Failed Locally"
        PROCESSED = "PROCESSED", "Processed"

    reference = models.CharField(
        max_length=255, unique=True
    )  # Unique reference for idempotency and tracking
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    recipient_code = models.CharField(max_length=100)
    transfer_code = models.CharField(max_length=100, null=True, blank=True)
    transfer_processor = models.CharField(max_length=20, choices=PaymentProcessors.choices, null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    reason = models.CharField(max_length=255, null=True, blank=True)
    error_log = models.TextField(null=True, blank=True)
    wallet_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )  # May be Wallet, InternalAccount, etc. depending on the transaction context.
    wallet_id = models.UUIDField(null=True, blank=True)
    wallet = GenericForeignKey("wallet_type", "wallet_id")
    transfer_request = models.ForeignKey(
        "wallet.WithdrawalRequest",
        on_delete=models.CASCADE,
        related_name="transfer_outbox_events",
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"Transfer {self.reference} - {self.status}"

    class Meta:
        db_table = "transfer_outbox_events"
        ordering = ["created_datetime"]


class YouverifyWebhookOutboxEvent(UUIDPrimaryKeyModelMixin, SoftDeleteModelMixin, DateHistoryModelMixin, models.Model):
    """Holds the webhook from YouVerify entity verification call"""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Background task processing"
        PROCESSING = "PROCESSING", "Currently being processed"
        FAILED = "FAILED", "Failed Locally"
        PROCESSED = "PROCESSED", "Processed"

    kyc_request_id = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    event_type = models.CharField(
        max_length=255, help_text="Type of the YouVerify webhook event, such as 'identity.completed'"
    )
    error_message = models.TextField(null=True, blank=True)
    payload = models.JSONField()

    def __str__(self):
        return f"YouVerify Webhook - {self.status}"

    class Meta:
        db_table = "youverify_webhook_events"
        ordering = ["created_datetime"]
