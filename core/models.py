# models.py

from typing import ClassVar

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from .mixins import (
    DateHistoryModelMixin,
    SoftDeleteModelMixin,
    UUIDPrimaryKeyModelMixin,
)

User = get_user_model()


class OutboxEvent(
    UUIDPrimaryKeyModelMixin, SoftDeleteModelMixin, DateHistoryModelMixin, models.Model
):
    """Stores outbox events waiting to be dispatched safely to an event bus."""

    event_type = models.CharField(max_length=255)
    payload = models.JSONField()
    processed = models.BooleanField(default=False)

    class Meta:
        db_table = "outbox_events"
        ordering = ["created_datetime"]


class PaystackWebhookEvent(
    UUIDPrimaryKeyModelMixin, SoftDeleteModelMixin, DateHistoryModelMixin, models.Model
):
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

    def __str__(self):
        return f"{self.event_type} - {self.event_id} ({self.status})"

    class Meta:
        db_table = "paystack_webhook_events"
        ordering = ["created_datetime"]


class TransferOutboxEvent(
    UUIDPrimaryKeyModelMixin, SoftDeleteModelMixin, DateHistoryModelMixin, models.Model
):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending API Call"
        PROCESSING = "PROCESSING", "API Call In Progress"
        SUBMITTED = (
            "SUBMITTED",
            "Sent to Paystack",
        )  # Paystack accepted it, now awaiting webhook
        FAILED = "FAILED", "Failed Locally"

    reference = models.CharField(
        max_length=255, unique=True
    )  # Unique reference for idempotency and tracking
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    recipient_code = models.CharField(max_length=100)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    reason = models.CharField(max_length=255, null=True, blank=True)
    paystack_transfer_code = models.CharField(max_length=100, null=True, blank=True)
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
