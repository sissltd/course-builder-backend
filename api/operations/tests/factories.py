"""Plain-function builders for the operations models, matching the style
of api.mie.tests.factories and api.courses.tests.factories."""

from datetime import timedelta
from decimal import Decimal
from itertools import count
from uuid import uuid4

from django.utils import timezone

from api.operations.enums import (
    CostCategory,
    EnrollmentStatus,
    PipelineJobStatus,
    PipelineStage,
    ProviderKind,
    ServicePriority,
    ServiceStatus,
)
from api.operations.models import (
    Enrollment,
    PipelineJob,
    ProductionCost,
    Provider,
    Service,
    ServiceHealthSample,
)

_sequence = count(1)


def make_service(*, name=None, priority=ServicePriority.NORMAL, **kwargs):
    defaults = {
        "name": name or f"Service {next(_sequence)}",
        "priority": priority,
    }
    defaults.update(kwargs)
    return Service.objects.create(**defaults)


def make_health_sample(
    *,
    service=None,
    status=ServiceStatus.OPERATIONAL,
    latency_ms=100,
    checked_at=None,
    **kwargs,
):
    defaults = {
        "service": service or make_service(),
        "status": status,
        "latency_ms": latency_ms,
        "checked_at": checked_at or timezone.now(),
    }
    defaults.update(kwargs)
    return ServiceHealthSample.objects.create(**defaults)


def make_provider(*, name=None, kind=ProviderKind.VOICE, **kwargs):
    defaults = {
        "name": name or f"Provider {next(_sequence)}",
        "kind": kind,
    }
    defaults.update(kwargs)
    return Provider.objects.create(**defaults)


def make_pipeline_job(
    *,
    course,
    stage=PipelineStage.TOPIC_INTAKE,
    status=PipelineJobStatus.QUEUED,
    **kwargs,
):
    defaults = {"course": course, "stage": stage, "status": status}
    defaults.update(kwargs)
    return PipelineJob.objects.create(**defaults)


def make_production_cost(
    *,
    amount="10.0000",
    category=CostCategory.VOICE,
    course=None,
    incurred_at=None,
    **kwargs,
):
    defaults = {
        "amount": Decimal(amount),
        "category": category,
        "course": course,
        "incurred_at": incurred_at or timezone.now(),
    }
    defaults.update(kwargs)
    return ProductionCost.objects.create(**defaults)


def make_enrollment(
    *,
    course,
    learner_reference=None,
    progress_percent=0,
    status=EnrollmentStatus.ACTIVE,
    enrolled_at=None,
    **kwargs,
):
    defaults = {
        "course": course,
        "learner_reference": learner_reference or uuid4().hex,
        "progress_percent": progress_percent,
        "status": status,
        "enrolled_at": enrolled_at or timezone.now() - timedelta(hours=1),
    }
    defaults.update(kwargs)
    return Enrollment.objects.create(**defaults)
