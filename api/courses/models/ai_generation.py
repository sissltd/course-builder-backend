from django.db import models
from api.courses.enums import (
    AIGenerationItemStatus,
    AIGenerationKind,
    AIGenerationPhase,
    AIGenerationStatus,
)
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class AIGenerationJob(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    creator = models.ForeignKey(
        "users.User", on_delete=models.CASCADE, related_name="ai_generation_jobs"
    )
    course = models.ForeignKey(
        "courses.Course",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ai_generation_jobs",
    )
    kind = models.CharField(max_length=16, choices=AIGenerationKind.choices)
    status = models.CharField(
        max_length=24,
        choices=AIGenerationStatus.choices,
        default=AIGenerationStatus.QUEUED,
    )
    stage = models.CharField(max_length=64, blank=True, default="")
    request_payload = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    provider = models.CharField(max_length=40, blank=True, default="")
    model = models.CharField(max_length=100, blank=True, default="")
    idempotency_key = models.CharField(max_length=255, blank=True, default="")
    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    cancel_requested = models.BooleanField(default=False)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_datetime"]
        constraints = [
            models.UniqueConstraint(
                fields=["creator", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_ai_generation_idempotency_key",
            )
        ]

    def __str__(self):
        return f"{self.kind}: {self.status}"


class AIGenerationItem(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    job = models.ForeignKey(
        AIGenerationJob, on_delete=models.CASCADE, related_name="items"
    )
    key = models.CharField(max_length=100)
    label = models.CharField(max_length=255)
    phase = models.CharField(max_length=32, choices=AIGenerationPhase.choices)
    status = models.CharField(
        max_length=16,
        choices=AIGenerationItemStatus.choices,
        default=AIGenerationItemStatus.PENDING,
    )
    order = models.PositiveIntegerField(default=0)
    target_type = models.CharField(max_length=40, blank=True, default="")
    target_id = models.UUIDField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["order", "created_datetime"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "key"], name="unique_ai_generation_item_key"
            )
        ]

    def __str__(self):
        return self.label
