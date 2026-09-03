"""Operational domain: production cost, service health, and the AI pipeline.

These back the admin dashboards. Each model is written so the numbers on
those screens are *measured* rather than asserted - a tile with no rows
behind it reads as zero, which is honest, instead of a hardcoded figure
that looks like data.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from api.operations.enums import (
    CostCategory,
    EnrollmentStatus,
    PipelineJobStatus,
    PipelineStage,
    ProviderKind,
    ServicePriority,
    ServiceStatus,
)
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class ProductionCost(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One recorded spend against producing a course.

    Deliberately an append-only ledger of individual charges rather than a
    running total on Course: the admin screens need cost per day, per
    category and per course, and only itemised rows can answer all three.
    `course` is nullable so platform-level spend (storage, idle capacity)
    can be recorded without inventing a course to hang it on.
    """

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="production_costs",
        help_text=_(
            "Course this spend belongs to. Null for platform-wide costs "
            "not attributable to one course."
        ),
    )
    category = models.CharField(
        verbose_name=_("Category"),
        max_length=20,
        choices=CostCategory.choices,
        default=CostCategory.OTHER,
        help_text=_("What the money was spent on."),
    )
    amount = models.DecimalField(
        verbose_name=_("Amount"),
        max_digits=12,
        decimal_places=4,
        help_text=_(
            "Cost in platform currency. Four decimal places because "
            "per-token and per-second provider rates are fractions of a "
            "cent, and rounding each row would drift the daily total."
        ),
    )
    provider = models.ForeignKey(
        "operations.Provider",
        verbose_name=_("Provider"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="costs",
        help_text=_("External provider that billed this, when applicable."),
    )
    incurred_at = models.DateTimeField(
        verbose_name=_("Incurred At"),
        help_text=_(
            "When the spend happened. Separate from created_datetime so a "
            "late-arriving provider invoice lands on the right day."
        ),
    )
    note = models.CharField(
        verbose_name=_("Note"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Free-text detail, e.g. the provider's line-item id."),
    )

    class Meta:
        verbose_name = _("Production Cost")
        verbose_name_plural = _("Production Costs")
        ordering = ["-incurred_at"]
        indexes = [
            models.Index(fields=["-incurred_at"], name="opscost_incurred_idx"),
            models.Index(fields=["course", "-incurred_at"], name="opscost_course_idx"),
            models.Index(fields=["category"], name="opscost_category_idx"),
        ]

    def __str__(self):
        return f"{self.category} {self.amount} @ {self.incurred_at}"


class Service(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A dependency shown on the System Health table.

    A registry row rather than a hardcoded list, so operations can add a
    provider without a deploy. `is_active` hides a retired service without
    deleting its history.
    """

    name = models.CharField(
        verbose_name=_("Name"),
        max_length=100,
        unique=True,
        help_text=_("Display name, e.g. 'API Gateway'."),
    )
    priority = models.CharField(
        verbose_name=_("Priority"),
        max_length=10,
        choices=ServicePriority.choices,
        default=ServicePriority.NORMAL,
        help_text=_("How urgently degradation here needs attention."),
    )
    is_active = models.BooleanField(
        verbose_name=_("Is Active"),
        default=True,
        help_text=_("Inactive services keep their history but leave the table."),
    )
    display_order = models.PositiveSmallIntegerField(
        verbose_name=_("Display Order"),
        default=0,
        help_text=_("Row order on the System Health table."),
    )

    class Meta:
        verbose_name = _("Service")
        verbose_name_plural = _("Services")
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


class ServiceHealthSample(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One probe result for a service.

    Uptime is computed as the share of samples that were OPERATIONAL over
    a window, so it is a measurement rather than a stored figure someone
    has to remember to update. A service with no samples reports no
    uptime rather than a misleading 100%.
    """

    service = models.ForeignKey(
        "operations.Service",
        verbose_name=_("Service"),
        on_delete=models.CASCADE,
        related_name="samples",
        help_text=_("Service this probe was against."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=15,
        choices=ServiceStatus.choices,
        help_text=_("Result of the probe."),
    )
    latency_ms = models.PositiveIntegerField(
        verbose_name=_("Latency (ms)"),
        null=True,
        blank=True,
        help_text=_("Round-trip time. Null when the probe never connected."),
    )
    checked_at = models.DateTimeField(
        verbose_name=_("Checked At"),
        help_text=_("When the probe ran."),
    )

    class Meta:
        verbose_name = _("Service Health Sample")
        verbose_name_plural = _("Service Health Samples")
        ordering = ["-checked_at"]
        indexes = [
            models.Index(
                fields=["service", "-checked_at"], name="opshealth_service_idx"
            ),
            models.Index(fields=["-checked_at"], name="opshealth_checked_idx"),
        ]

    def __str__(self):
        return f"{self.service_id} {self.status} @ {self.checked_at}"


class Provider(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """An external generation provider used by the pipeline.

    `current_load_percent` and `current_queue_depth` are last-known
    readings written by whatever polls the provider, not derived here -
    the dashboard reads them straight through, and `readings_updated_at`
    tells the reader how stale they are rather than pretending they are live.
    """

    name = models.CharField(
        verbose_name=_("Name"),
        max_length=100,
        unique=True,
        help_text=_("Display name, e.g. 'WellSaid Labs'."),
    )
    kind = models.CharField(
        verbose_name=_("Kind"),
        max_length=10,
        choices=ProviderKind.choices,
        help_text=_("What this provider supplies."),
    )
    is_active = models.BooleanField(
        verbose_name=_("Is Active"),
        default=True,
        help_text=_("Inactive providers stop being offered new jobs."),
    )
    current_load_percent = models.PositiveSmallIntegerField(
        verbose_name=_("Current Load (%)"),
        null=True,
        blank=True,
        help_text=_("Last known utilisation, 0-100. Null when never polled."),
    )
    current_queue_depth = models.PositiveIntegerField(
        verbose_name=_("Current Queue Depth"),
        null=True,
        blank=True,
        help_text=_("Last known jobs waiting at the provider."),
    )
    readings_updated_at = models.DateTimeField(
        verbose_name=_("Readings Updated At"),
        null=True,
        blank=True,
        help_text=_("When load/queue were last written, so staleness is visible."),
    )

    class Meta:
        verbose_name = _("Provider")
        verbose_name_plural = _("Providers")
        ordering = ["kind", "name"]

    def __str__(self):
        return self.name


class PipelineJob(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One unit of AI production work at one stage.

    A course moves through the stages as a series of jobs, so the funnel
    counts on the pipeline screen are just counts of jobs per stage, and
    average pipeline time is measurable from the timestamps rather than
    estimated.
    """

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="pipeline_jobs",
        help_text=_("Course being produced."),
    )
    stage = models.CharField(
        verbose_name=_("Stage"),
        max_length=25,
        choices=PipelineStage.choices,
        help_text=_("Which stage of production this job performs."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=PipelineJobStatus.choices,
        default=PipelineJobStatus.QUEUED,
        help_text=_("Where the job currently sits."),
    )
    provider = models.ForeignKey(
        "operations.Provider",
        verbose_name=_("Provider"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs",
        help_text=_("Provider handling this job, when one is involved."),
    )
    attempts = models.PositiveSmallIntegerField(
        verbose_name=_("Attempts"),
        default=0,
        help_text=_("Delivery attempts made, for the Failed/Retry tile."),
    )
    started_at = models.DateTimeField(
        verbose_name=_("Started At"), null=True, blank=True
    )
    finished_at = models.DateTimeField(
        verbose_name=_("Finished At"), null=True, blank=True
    )
    last_error = models.CharField(
        verbose_name=_("Last Error"),
        max_length=500,
        blank=True,
        default="",
        help_text=_("Failure detail from the most recent attempt."),
    )

    class Meta:
        verbose_name = _("Pipeline Job")
        verbose_name_plural = _("Pipeline Jobs")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(fields=["stage", "status"], name="opsjob_stage_status_idx"),
            models.Index(fields=["status", "-created_datetime"], name="opsjob_status_idx"),
            models.Index(fields=["course", "stage"], name="opsjob_course_stage_idx"),
        ]

    def __str__(self):
        return f"{self.stage}/{self.status} for {self.course_id}"


class Enrollment(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A learner enrolled on a published course.

    Backs Total Enrollment and, with `progress_percent`, Average
    Completion Rate. `learner` is a plain identifier rather than a FK to
    users: learners are not platform accounts in this service, and the
    enrolment may originate from an external marketplace.
    """

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="enrollments",
        help_text=_("Course enrolled on."),
    )
    learner_reference = models.CharField(
        verbose_name=_("Learner Reference"),
        max_length=255,
        help_text=_(
            "Opaque learner identifier from the originating channel. Not a "
            "platform user id - learners do not have accounts here."
        ),
    )
    channel = models.CharField(
        verbose_name=_("Channel"),
        max_length=20,
        blank=True,
        default="",
        help_text=_("Where the enrolment came from, e.g. the marketplace name."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.ACTIVE,
        help_text=_("Whether the learner is active, finished, or dropped out."),
    )
    progress_percent = models.PositiveSmallIntegerField(
        verbose_name=_("Progress (%)"),
        default=0,
        help_text=_("Completion 0-100. Averaged for the completion-rate tile."),
    )
    enrolled_at = models.DateTimeField(
        verbose_name=_("Enrolled At"),
        help_text=_("When the learner enrolled."),
    )
    completed_at = models.DateTimeField(
        verbose_name=_("Completed At"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("Enrollment")
        verbose_name_plural = _("Enrollments")
        ordering = ["-enrolled_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "learner_reference"],
                name="unique_learner_per_course",
            ),
        ]
        indexes = [
            models.Index(fields=["course", "status"], name="opsenrol_course_idx"),
            models.Index(fields=["-enrolled_at"], name="opsenrol_when_idx"),
        ]

    def __str__(self):
        return f"{self.learner_reference} on {self.course_id}"
