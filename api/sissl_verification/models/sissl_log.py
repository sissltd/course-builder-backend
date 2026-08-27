from django.conf import settings
from django.db import models

# Using the UUID and Date History mixins so we get the id and timestamps for free.
# We deliberately do NOT use SoftDeleteModelMixin — SISSLLog rows are append-only
# forensic records used for cost reconciliation, debugging, and audit.
from core.mixins import (
    DateHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


# >>>>>>>>>>>>>>>>>>>>>>>>> SISSLLog Model <<<<<<<<<<<<<<<<<<<<<<<<<<
class SISSLLog(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, models.Model):
    """
    A forensic + cost record of every SISSL call we make.

    One row is written per attempt — successes AND failures — so we can:
      - Reconcile our spend against SISSL's invoice
      - Trace what happened when a user reports "verification didn't work"
      - Monitor health (error rate / latency per kind)

    PII (BVN, NIN, base64 image, photo URL) is NEVER stored here.
    The service layer redacts at the write site — request_summary and
    response_summary should only carry presence flags / scores / statuses.
    """

    # >>>>>>>>>>>>>>>>>>>> Choice Classes <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    class Kind(models.TextChoices):
        """
        Which SISSL endpoint produced this row.
        """

        LIVENESS = "liveness", "Liveness"
        BVN = "bvn", "BVN lookup"
        NIN = "nin", "NIN lookup"

    class Status(models.TextChoices):
        """
        Outcome of the HTTP call to SISSL itself.

        SUCCESS  -> SISSL returned 2xx with a parsable body. The user may still
                    have failed the verification (e.g. liveness score below
                    threshold) — that's reflected in response_summary.

        ERROR    -> SISSL was unreachable / returned non-2xx / timed out.
                    Used to alert + debug vendor health.
        """

        SUCCESS = "success", "Success"
        ERROR = "error", "Error"


    # >>>>>>>>>>>>>>>>>>>> Identity <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    # The user this call was made on behalf of. Nullable in case a future
    # system-level call (e.g. an admin retry, a health-check probe) has no
    # user attached.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sissl_logs",
        help_text="The user this SISSL call was made on behalf of (nullable for system calls)",
    )

    # Which endpoint we hit
    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        db_index=True,
        help_text="Which SISSL endpoint produced this row",
    )

    # HTTP outcome (not the verification outcome — see response_summary for that)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        db_index=True,
        help_text="Outcome of the HTTP call (not the verification outcome)",
    )


    # >>>>>>>>>>>>>>>>>>>> Redacted Summaries <<<<<<<<<<<<<<<<<<<<<<<<<<
    """
    Both summary fields are JSONFields populated by the service layer.

    The pattern is presence-only — write {"photo_present": True}, NEVER
    {"photo": "https://..."}. This is enforced at the write site (the
    service); the DB layer cannot police it.
    """

    request_summary = models.JSONField(
        default=dict,
        blank=True,
        help_text="Redacted summary of the request — presence flags only, NO raw PII",
    )

    response_summary = models.JSONField(
        default=dict,
        blank=True,
        help_text="Redacted summary of the response — scores/statuses only, NO raw PII",
    )


    # >>>>>>>>>>>>>>>>>>>> Operational Fields <<<<<<<<<<<<<<<<<<<<<<<<<<
    # Round-trip latency in milliseconds — used to build the SLA dashboard
    latency_ms = models.PositiveIntegerField(
        default=0,
        help_text="HTTP round-trip latency in milliseconds",
    )

    # When status=ERROR, the message that came back (or the timeout / parse error)
    error_message = models.TextField(
        blank=True,
        default="",
        help_text="Error message — populated only when status=error",
    )


    # >>>>>>>>>>>>>>>>>>>> Cost Reconciliation <<<<<<<<<<<<<<<<<<<<<<<<<
    # Optional per-call cost. Populated either from billing rules or by
    # reconciling against SISSL's invoice. Nullable on insert.
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        blank=True,
        null=True,
        help_text="Per-call cost — populated from billing rules or reconciled against vendor invoice",
    )


    class Meta:
        db_table = "sissl_logs"
        ordering = ["-created_datetime"]
        verbose_name = "SISSL Log"
        verbose_name_plural = "SISSL Logs"
        indexes = [
            models.Index(fields=["kind", "-created_datetime"]),
            models.Index(fields=["user", "-created_datetime"]),
            models.Index(fields=["status", "-created_datetime"]),
        ]

    def __str__(self):
        who = self.user.email if self.user else "system"
        return f"SISSLLog: {self.kind} :: {self.status} :: {who} :: {self.created_datetime:%Y-%m-%d %H:%M}"
