from django.contrib import admin

from api.production.models import (
    ChannelMapping,
    ProductionAsset,
    ProductionRun,
    ProductionScene,
    PronunciationEntry,
)


@admin.register(ProductionRun)
class ProductionRunAdmin(admin.ModelAdmin):
    list_display = ("course", "kind", "status", "quote_amount", "spent_amount", "attempts", "created_datetime")
    list_filter = ("kind", "status")
    search_fields = ("course__title",)
    list_select_related = ("course",)
    readonly_fields = ("spent_amount", "attempts", "started_at", "heartbeat_at", "dispatched_at", "finished_at")


@admin.register(ProductionScene)
class ProductionSceneAdmin(admin.ModelAdmin):
    list_display = ("lesson", "order", "scene_type", "run")
    list_filter = ("scene_type",)
    list_select_related = ("lesson", "run")


@admin.register(ProductionAsset)
class ProductionAssetAdmin(admin.ModelAdmin):
    list_display = ("kind", "course", "lesson", "size_bytes", "created_datetime")
    list_filter = ("kind",)
    search_fields = ("course__title", "key")
    list_select_related = ("course", "lesson")
    readonly_fields = ("key", "file_key", "data")


@admin.register(PronunciationEntry)
class PronunciationEntryAdmin(admin.ModelAdmin):
    list_display = ("term", "spoken_as", "course")
    search_fields = ("term", "course__title")
    list_select_related = ("course",)


@admin.register(ChannelMapping)
class ChannelMappingAdmin(admin.ModelAdmin):
    list_display = ("channel", "version", "delivery_method", "is_active", "created_datetime")
    list_filter = ("channel", "is_active")
    # Versions are never edited: a change is a new version, saved through
    # the admin API so it is validated and audited.
    readonly_fields = ("channel", "version", "delivery_method", "target_schema", "field_map", "response_id_path", "created_by")
