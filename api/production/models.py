"""The Production Engine's own records: one run per production attempt, and
the storyboard scenes it plans for each lesson.

Each step a run performs is also a `operations.PipelineJob` row (with `run`
and `lesson` set), and each paid call an `operations.ProductionCost` row, so
the existing APE Pipeline dashboard and cost charts read real work.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from api.courses.enums import DistributionChannel
from api.production.enums import (
    ACTIVE_RUN_STATUSES,
    AssetKind,
    DeliveryMethod,
    ProductionRunStatus,
    RunKind,
    SceneType,
)
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class ProductionRun(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One attempt by the Production Engine to produce a course's video."""

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="production_runs",
        help_text=_("Course being produced."),
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Requested By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_production_runs",
        help_text=_(
            "Whose action handed the course to the engine: the reviewer who "
            "approved a crawler course's text, or the creator who declined to "
            "make the video. The run's activity is logged on their record."
        ),
    )
    kind = models.CharField(
        verbose_name=_("Kind"),
        max_length=10,
        choices=RunKind.choices,
        default=RunKind.VIDEO,
        help_text=_("Video production (and rework), or the final package of a published course."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=ProductionRunStatus.choices,
        default=ProductionRunStatus.QUEUED,
    )
    instructions = models.JSONField(
        verbose_name=_("Rework Instructions"),
        default=dict,
        blank=True,
        help_text=_(
            "For a rework run, what reviewers asked the engine to redo: "
            '{"lessons": {"<lesson id>": ["REVOICE", ...]}, "flags": ["<flag id>", ...]}. '
            "Empty for a first production."
        ),
    )
    quote_amount = models.DecimalField(
        verbose_name=_("Quote"),
        max_digits=10,
        decimal_places=2,
        help_text=_("Estimated cost of the whole run in USD, made before any spend."),
    )
    budget_amount = models.DecimalField(
        verbose_name=_("Budget"),
        max_digits=10,
        decimal_places=2,
        help_text=_("The per-course budget in force when the run was quoted, in USD."),
    )
    spent_amount = models.DecimalField(
        verbose_name=_("Spent"),
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
        help_text=_("Actual spend so far, the sum of this run's ProductionCost rows."),
    )
    status_reason = models.CharField(
        verbose_name=_("Status Reason"),
        max_length=500,
        blank=True,
        default="",
        help_text=_("Why the run is blocked, failed or cancelled."),
    )
    attempts = models.PositiveSmallIntegerField(
        verbose_name=_("Attempts"),
        default=0,
        help_text=_("Times a worker has started this run."),
    )
    started_at = models.DateTimeField(verbose_name=_("Started At"), null=True, blank=True)
    dispatched_at = models.DateTimeField(
        verbose_name=_("Dispatched At"),
        null=True,
        blank=True,
        help_text=_("When the run was last handed to a worker, so the sweep does not re-send it."),
    )
    heartbeat_at = models.DateTimeField(
        verbose_name=_("Heartbeat At"),
        null=True,
        blank=True,
        help_text=_("Last sign of life from the worker; a stale one is re-dispatched."),
    )
    finished_at = models.DateTimeField(verbose_name=_("Finished At"), null=True, blank=True)

    class Meta:
        verbose_name = _("Production Run")
        verbose_name_plural = _("Production Runs")
        ordering = ["-created_datetime"]
        constraints = [
            models.UniqueConstraint(
                fields=["course"],
                condition=Q(status__in=ACTIVE_RUN_STATUSES),
                name="production_one_active_run_per_course",
            ),
        ]
        indexes = [
            # The admin list (newest first, filtered by status) and the
            # dispatcher's sweep of queued / running runs.
            models.Index(fields=["status", "-created_datetime"], name="prodrun_status_idx"),
            models.Index(fields=["course", "-created_datetime"], name="prodrun_course_idx"),
        ]

    def __str__(self):
        return f"{self.course_id} {self.status}"


class ProductionScene(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One scene of a lesson's storyboard.

    `narration` is a verbatim slice of the lesson's approved script: the
    engine never rewrites approved words while producing video. The scenes
    of a lesson, joined in order, are exactly its script's sentences.
    """

    run = models.ForeignKey(
        ProductionRun,
        verbose_name=_("Run"),
        on_delete=models.CASCADE,
        related_name="scenes",
    )
    lesson = models.ForeignKey(
        "courses.Lesson",
        verbose_name=_("Lesson"),
        on_delete=models.CASCADE,
        related_name="production_scenes",
    )
    order = models.PositiveSmallIntegerField(verbose_name=_("Order"))
    scene_type = models.CharField(
        verbose_name=_("Scene Type"),
        max_length=12,
        choices=SceneType.choices,
        help_text=_("Template the scene is rendered with."),
    )
    narration = models.TextField(
        verbose_name=_("Narration"),
        help_text=_("Exact sentences from the approved script spoken over this scene."),
    )
    on_screen_text = models.CharField(
        verbose_name=_("On-screen Text"),
        max_length=500,
        blank=True,
        default="",
        help_text=_("Short text shown on screen, signalling the key point."),
    )
    visual_brief = models.TextField(
        verbose_name=_("Visual Brief"),
        blank=True,
        default="",
        help_text=_("What the scene shows, for the visual step."),
    )
    bullets = models.JSONField(
        verbose_name=_("Bullets"),
        default=list,
        blank=True,
        help_text=_(
            "Short lines drawn on the scene: key points, recap items, diagram "
            "steps (in order), or comparison rows written 'left | right'."
        ),
    )
    code = models.TextField(
        verbose_name=_("Code"),
        blank=True,
        default="",
        help_text=_("The code shown on a CODE scene."),
    )
    script_hash = models.CharField(
        verbose_name=_("Script Hash"),
        max_length=64,
        help_text=_(
            "SHA-256 of the lesson script the scene was planned from. A "
            "changed script means the storyboard is stale and is planned again."
        ),
    )

    class Meta:
        verbose_name = _("Production Scene")
        verbose_name_plural = _("Production Scenes")
        ordering = ["lesson_id", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "lesson", "order"], name="production_scene_order_uniq"
            ),
        ]

    def __str__(self):
        return f"{self.lesson_id} #{self.order} {self.scene_type}"


class ProductionAsset(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A file the engine made, stored once under the hash of its inputs.

    Before any step does paid or slow work it looks its inputs' hash up
    here: a hit is reused, so a retried run, or a rework that touches one
    lesson, pays only for what changed. The course is part of every hash,
    so courses never share files.
    """

    key = models.CharField(
        verbose_name=_("Input Hash"),
        max_length=64,
        unique=True,
        help_text=_("SHA-256 of everything that went into the file."),
    )
    kind = models.CharField(verbose_name=_("Kind"), max_length=12, choices=AssetKind.choices)
    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="production_assets",
    )
    lesson = models.ForeignKey(
        "courses.Lesson",
        verbose_name=_("Lesson"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="production_assets",
    )
    file_key = models.CharField(
        verbose_name=_("File Key"), max_length=500, help_text=_("Object key in storage.")
    )
    content_type = models.CharField(verbose_name=_("Content Type"), max_length=100)
    size_bytes = models.PositiveBigIntegerField(verbose_name=_("Size"), default=0)
    duration_ms = models.PositiveIntegerField(
        verbose_name=_("Duration (ms)"), null=True, blank=True
    )
    data = models.JSONField(
        verbose_name=_("Data"),
        default=dict,
        blank=True,
        help_text=_(
            "What later steps need from this file: narration timings, the "
            "measured quality evidence, the caption and transcript keys."
        ),
    )

    class Meta:
        verbose_name = _("Production Asset")
        verbose_name_plural = _("Production Assets")
        ordering = ["-created_datetime"]
        indexes = [
            # The package downloads: a course's newest assets of some kinds.
            models.Index(fields=["course", "kind", "-created_datetime"], name="prodasset_course_kind_idx"),
        ]

    def __str__(self):
        return f"{self.kind} {self.key[:12]}"


class PronunciationEntry(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """How the narrator should say a term in one course.

    Only the text sent to the voice changes; captions and transcripts keep
    the approved spelling. Added from a reviewer's PRONUNCIATION flag (one
    `term = spoken form` per line of its note).
    """

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="pronunciations",
    )
    term = models.CharField(verbose_name=_("Term"), max_length=100)
    spoken_as = models.CharField(
        verbose_name=_("Spoken As"),
        max_length=200,
        help_text=_("What the voice is given instead, e.g. 'koo-ber-NET-eez'."),
    )

    class Meta:
        verbose_name = _("Pronunciation")
        verbose_name_plural = _("Pronunciations")
        ordering = ["term"]
        constraints = [
            models.UniqueConstraint(fields=["course", "term"], name="production_pronunciation_uniq"),
        ]

    def __str__(self):
        return f"{self.term} -> {self.spoken_as}"


class ChannelMapping(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """How one distribution channel receives a course, as versioned data.

    The engine builds one canonical package per course. A mapping turns it
    into the channel's shape (`field_map`), checks the result against the
    channel's own schema (`target_schema`, JSON Schema), and delivers it by
    `delivery_method`. Adding or changing a platform is a new mapping
    version, not new code. One version per channel is active.
    """

    channel = models.CharField(
        verbose_name=_("Channel"), max_length=10, choices=DistributionChannel.choices
    )
    version = models.PositiveSmallIntegerField(verbose_name=_("Version"))
    delivery_method = models.CharField(
        verbose_name=_("Delivery Method"), max_length=10, choices=DeliveryMethod.choices
    )
    target_schema = models.JSONField(
        verbose_name=_("Target Schema"),
        default=dict,
        help_text=_("JSON Schema of what the channel accepts."),
    )
    field_map = models.JSONField(
        verbose_name=_("Field Map"),
        default=dict,
        help_text=_(
            "Target field path -> rule. A rule is {'from': '<package path>'}, "
            "optionally with 'transform': [...] and 'default'; {'const': value}; "
            "or {'each': '<package list path>', 'map': {<nested field map>}}."
        ),
    )
    response_id_path = models.CharField(
        verbose_name=_("Response Id Path"),
        max_length=100,
        blank=True,
        default="",
        help_text=_("API push only: where the channel's course id sits in its response, e.g. 'data.id'."),
    )
    is_active = models.BooleanField(verbose_name=_("Active"), default=False)
    notes = models.TextField(verbose_name=_("Notes"), blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Created By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = _("Channel Mapping")
        verbose_name_plural = _("Channel Mappings")
        ordering = ["channel", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["channel", "version"], name="production_mapping_version_uniq"),
            models.UniqueConstraint(
                fields=["channel"],
                condition=Q(is_active=True),
                name="production_mapping_one_active",
            ),
        ]

    def __str__(self):
        return f"{self.channel} v{self.version}"
