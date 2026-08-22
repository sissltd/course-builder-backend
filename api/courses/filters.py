import django_filters

from api.catalog.enums import TrackPreference
from api.courses.models import Course
from api.users.enums import QueueTrackFilter


#: Same mapping as course_service.QUEUE_TRACK_FILTER_TO_CATEGORY_TRACK_PREFERENCE
#: - duplicated here rather than imported to avoid a filters.py -> services.py
#: dependency; both are small, static, and unlikely to drift independently.
_TRACK_FILTER_TO_CATEGORY_TRACK_PREFERENCE = {
    QueueTrackFilter.CREATOR_TRACK: TrackPreference.CREATOR_PREFERRED,
    QueueTrackFilter.AI_TRACK: TrackPreference.AI_PREFERRED,
}


class CourseReviewQueueFilter(django_filters.FilterSet):
    """Filters for the reviewer queue. `track` is an explicit ad hoc
    override independent of the reviewer's stored QueueBehaviourPreference -
    see CourseReviewViewSet.get_queryset for how the two interact."""

    track = django_filters.ChoiceFilter(
        choices=QueueTrackFilter.choices,
        method="filter_track",
        label="Track",
    )

    class Meta:
        model = Course
        fields = {
            "status": ["exact"],
            "category": ["exact"],
        }

    def filter_track(self, queryset, name, value):
        category_track_preference = _TRACK_FILTER_TO_CATEGORY_TRACK_PREFERENCE.get(
            value
        )
        if category_track_preference is None:
            return queryset
        return queryset.filter(category__track_preference=category_track_preference)
