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
        default=QueueSortOrder.ALL,
        help_text=_(
            "Default view applied to the review queue - an ordering, or a "
            "recent-window filter sorted oldest-first."
        ),
    )
    auto_advance_enabled = models.BooleanField(
        verbose_name=_("Auto Advance Enabled"),
        default=False,
        help_text=_(
            "Frontend-only: automatically advance to the next queue item "
            "after approving or rejecting a course."
        ),
    )
    show_ai_track = models.BooleanField(
        verbose_name=_("Show AI Track Courses"),
        default=False,
        help_text=_("Include APE-produced courses in the reviewer's queue."),
    )
    show_creator_track = models.BooleanField(
        verbose_name=_("Show Creator Track Courses"),
        default=False,
        help_text=_("Include human-submitted courses in the reviewer's queue."),
    )
    show_both_track = models.BooleanField(
        verbose_name=_("Show Both Tracks"),
        default=True,
        help_text=_(
            "Include both AI and human-created courses. Wins over the two "
            "toggles above when on, matching the design's third switch. "
            "Defaults on so a new reviewer sees the whole queue rather "
            "than an empty one."
        ),
    )

    class Meta:
        verbose_name = _("Queue Behaviour Preference")
        verbose_name_plural = _("Queue Behaviour Preferences")

    @property
    def effective_track_filter(self) -> str:
        """Narrowing implied by the three toggles.

        The design presents three independent switches, which makes some
        combinations ambiguous and one of them empty. Resolved as:

        * `show_both_track` on, or both single toggles on -> ALL
        * exactly one single toggle on -> that track
        * nothing on -> NONE, an intentionally empty queue

        NONE is represented rather than silently coerced to ALL: a
        reviewer who turned every track off asked for an empty queue, and
        quietly showing them everything would be the wrong answer.
        """

        if self.show_both_track or (self.show_ai_track and self.show_creator_track):
            return QueueTrackFilter.ALL
        if self.show_ai_track:
            return QueueTrackFilter.AI_TRACK
        if self.show_creator_track:
            return QueueTrackFilter.CREATOR_TRACK
        return QueueTrackFilter.NONE

    def __str__(self):
        """Use the owning user's id as the human-readable label."""

        return f"QueueBehaviourPreference({self.user_id})"
