"""What every step of a run writes down, and the checks between steps.

Each step done is a `PipelineJob` (the APE Pipeline dashboard), each paid
call a `ProductionCost` added to the run's spend. Between steps a run stops
if an admin cancelled it, if the engine was switched off (it goes back to
the queue), or if its spend reached the budget (it is blocked).
"""

from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from api.authentication.services.activity_service import log_activity
from api.authorization import codenames
from api.authorization.services import permission_service
from api.notification.models import Notification
from api.operations.enums import PipelineJobStatus, ProviderKind
from api.operations.models import PipelineJob, ProductionCost, Provider
from api.platform.services import platform_settings_service
from api.production.enums import ProductionRunStatus
from api.production.models import ProductionRun
from api.production.tracing import Tracer
from api.users.enums import UserActivityCategoryEnums


class Stop(Exception):
    """The run must stop where it is (switched off, cancelled, budget
    reached); its status is already set."""


class RunLedger:
    def __init__(self, run: ProductionRun):
        self.run = run
        self.tracer = Tracer(run=run)
        self._providers: dict[str, Provider] = {}

    def provider(self, name: str, kind: str = ProviderKind.VOICE) -> Provider:
        """The registry row for `name`, so steps and costs name it."""

        if name not in self._providers:
            self._providers[name], _ = Provider.objects.get_or_create(
                name=name, defaults={"kind": kind}
            )
        return self._providers[name]

    def step(
        self,
        *,
        stage: str,
        step: str,
        started_at,
        lesson=None,
        provider: Provider | None = None,
        error: str = "",
    ) -> None:
        PipelineJob.objects.create(
            course=self.run.course,
            run=self.run,
            lesson=lesson,
            stage=stage,
            step=step,
            status=PipelineJobStatus.FAILED if error else PipelineJobStatus.COMPLETED,
            provider=provider,
            attempts=1,
            started_at=started_at,
            finished_at=timezone.now(),
            last_error=error[:500],
        )
        self.heartbeat()

    def charge(self, *, category: str, amount: Decimal, provider: Provider | None, note: str) -> None:
        if not amount:
            return
        with transaction.atomic():
            ProductionCost.objects.create(
                course=self.run.course,
                category=category,
                amount=amount,
                provider=provider,
                incurred_at=timezone.now(),
                note=note[:255],
            )
            self.run.spent_amount += amount
            self.run.save(update_fields=["spent_amount", "updated_datetime"])

    def heartbeat(self) -> None:
        self.run.heartbeat_at = timezone.now()
        ProductionRun.objects.filter(pk=self.run.pk).update(
            heartbeat_at=self.run.heartbeat_at, updated_datetime=self.run.heartbeat_at
        )

    def checkpoint(self) -> None:
        """Between steps: stop if cancelled, paused by the switch, or over
        budget."""

        current = ProductionRun.objects.only("status").get(pk=self.run.pk).status
        if current != ProductionRunStatus.RUNNING:
            raise Stop()
        if not platform_settings_service.get_settings().production_enabled:
            ProductionRun.objects.filter(pk=self.run.pk, status=ProductionRunStatus.RUNNING).update(
                status=ProductionRunStatus.QUEUED,
                status_reason="Paused: the Production Engine is switched off.",
                dispatched_at=None,
                updated_datetime=timezone.now(),
            )
            raise Stop()
        if self.run.spent_amount >= self.run.budget_amount:
            finish(
                self.run,
                ProductionRunStatus.BLOCKED,
                reason=f"Spend reached the per-course budget of ${self.run.budget_amount}.",
            )
            alert(self.run, title="Production blocked by budget", content=self.run.status_reason)
            raise Stop()


TOKENS_PER_MILLION = Decimal("1000000")


def text_cost(input_tokens: int, output_tokens: int) -> Decimal:
    """What a call to the text model cost, at the configured list prices."""

    return (
        Decimal(input_tokens) * Decimal(settings.PRODUCTION_TEXT_INPUT_USD_PER_MTOK)
        + Decimal(output_tokens) * Decimal(settings.PRODUCTION_TEXT_OUTPUT_USD_PER_MTOK)
    ) / TOKENS_PER_MILLION


def finish(run: ProductionRun, status: str, *, reason: str) -> None:
    run.status = status
    run.status_reason = reason[:500]
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "status_reason", "finished_at", "updated_datetime"])


def log(run: ProductionRun, action: str, summary: str) -> None:
    """Engine activity goes on the record of whoever handed the course over."""

    if run.requested_by_id is None:
        return
    log_activity(
        user=run.requested_by,
        category=UserActivityCategoryEnums.PRODUCTION,
        action=action,
        summary=summary[:255],
        details={"production_run_id": str(run.id)},
        target=run.course,
    )


def alert(run: ProductionRun, *, title: str, content: str) -> None:
    """Tell everyone who can manage production, unless they switched the
    pipeline alert off."""

    receivers = list(
        permission_service.users_with_permission(codenames.PRODUCTION_MANAGE).exclude(
            notification_preference__mie_pipeline_alert=False
        )
    )
    if not receivers:
        return
    Notification.emit_in_app_notification(
        receivers=receivers,
        title=title,
        content=f"'{run.course.title}': {content}",
        metadata={"production_run_id": run.id, "course_id": run.course_id},
    )
