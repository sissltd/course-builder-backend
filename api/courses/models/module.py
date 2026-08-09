from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.mixins import (
    DateHistoryModelMixin,
    UserHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class Module(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin):
    """A top-level section of a Course (SCCS PRD Section 6.1: 4-12 per course).

    locked_by/lock_expires_at implement a simple TTL-based editing lock (SCCS
    PRD Section 14: "module-level locking prevent conflicts") - a REST
    acquire/release/heartbeat flow via module_lock_service, not real-time
    presence over WebSockets.
    """

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="modules",
        help_text=_("Course this module belongs to."),
    )
    title = models.CharField(
        verbose_name=_("Title"),
        max_length=255,
        help_text=_("Module title."),
    )
    order = models.PositiveIntegerField(
        verbose_name=_("Order"),
        help_text=_("Display order of this module within the course."),
    )
    locked_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Locked By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("User currently holding the edit lock on this module, if any."),
    )
    lock_expires_at = models.DateTimeField(
        verbose_name=_("Lock Expires At"),
        null=True,
        blank=True,
        help_text=_("When the current edit lock auto-expires."),
    )

    @property
    def is_locked(self) -> bool:
        """Whether this module currently has an active (unexpired) edit lock."""

        return bool(
            self.locked_by_id
            and self.lock_expires_at
            and self.lock_expires_at > timezone.now()
        )

    class Meta:
        verbose_name = _("Module")
        verbose_name_plural = _("Modules")
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "order"], name="unique_module_order_per_course"
            ),
        ]
        indexes = [
            models.Index(fields=["course", "order"], name="module_course_order_idx"),
        ]

    def __str__(self):
        """Use the module title as the human-readable label."""

        return self.title
