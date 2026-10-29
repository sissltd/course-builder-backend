from django.contrib import admin

from api.operations.models import (
    Enrollment,
    PipelineJob,
    ProductionCost,
    Provider,
    Service,
    ServiceHealthSample,
)


@admin.register(ProductionCost)
class ProductionCostAdmin(admin.ModelAdmin):
    list_display = ("category", "amount", "course", "provider", "incurred_at")
    list_filter = ("category", "provider")
    search_fields = ("course__title", "note")


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "priority", "is_active", "display_order")
    list_filter = ("priority", "is_active")
    search_fields = ("name",)


@admin.register(ServiceHealthSample)
class ServiceHealthSampleAdmin(admin.ModelAdmin):
    list_display = ("service", "status", "latency_ms", "checked_at")
    list_filter = ("status", "service")


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "kind",
        "is_active",
        "current_load_percent",
        "current_queue_depth",
        "readings_updated_at",
    )
    list_filter = ("kind", "is_active")
    search_fields = ("name",)


@admin.register(PipelineJob)
class PipelineJobAdmin(admin.ModelAdmin):
    list_display = ("stage", "status", "course", "provider", "attempts", "finished_at")
    list_filter = ("stage", "status", "provider")
    search_fields = ("course__title",)


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = (
        "learner_reference",
        "course",
        "channel",
        "status",
        "progress_percent",
        "enrolled_at",
    )
    list_filter = ("status", "channel")
    search_fields = ("learner_reference", "course__title")
