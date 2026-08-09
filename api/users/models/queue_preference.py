from django.db import models
from django.utils.translation import gettext_lazy as _

from api.users.enums import QueueSortOrder, QueueTrackFilter
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class QueueBehaviourPreference(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A Creator Reviewer's preferences for how their review queue behaves.

    Lazily provisioned via queue_preference_service.get_or_create, mirroring
    ReviewerAvailability - no row exists until a reviewer first opens their
    Queue Behaviour settings. auto_advance_enabled has no backend effect -
    it's a pure frontend UX toggle (auto-navigate to the next item after a
    decision) exposed here purely for storage/round-trip.
    """

    user = models.OneToOneField(
        "users.User",
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="queue_behaviour_preference",
        help_text=_("Reviewer this queue-behaviour preference belongs to."),
    )
    default_sort_order = models.CharField(
        verbose_name=_("Default Sort Order"),
        max_length=20,
        choices=QueueSortOrder.choices,
        default=QueueSortOrder.OLDEST_FIRST,
        help_text=_("Default ordering applied to the review queue."),
    )
    auto_advance_enabled = models.BooleanField(
        verbose_name=_("Auto Advance Enabled"),
        default=False,
        help_text=_(
            "Frontend-only: automatically advance to the next queue item "
            "after approving or rejecting a course."
        ),
    )
    track_filter = models.CharField(
        verbose_name=_("Track Filter"),
        max_length=20,
        choices=QueueTrackFilter.choices,
        default=QueueTrackFilter.ALL,
        help_text=_("Default track filter applied to the review queue."),
    )

    class Meta:
        verbose_name = _("Queue Behaviour Preference")
        verbose_name_plural = _("Queue Behaviour Preferences")

    def __str__(self):
        """Use the owning user's id as the human-readable label."""

        return f"QueueBehaviourPreference({self.user_id})"
