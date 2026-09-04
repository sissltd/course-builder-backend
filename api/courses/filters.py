import django_filters
from django.db.models import Q

from api.catalog.enums import TrackPreference
from api.courses.enums import CourseSourceType
from api.courses.models import Course
from api.reviews.enums import ReviewStage
from api.reviews.enums import ReviewActionType
from api.users.enums import QueueTrackFilter


#: Same mapping as course_service.QUEUE_TRACK_FILTER_TO_CATEGORY_TRACK_PREFERENCE
#: - duplicated here rather than imported to avoid a filters.py -> services.py
#: dependency; both are small, static, and unlikely to drift independently.
_TRACK_FILTER_TO_CATEGORY_TRACK_PREFERENCE = {
    QueueTrackFilter.CREATOR_TRACK: TrackPreference.CREATOR_PREFERRED,
    QueueTrackFilter.AI_TRACK: TrackPreference.AI_PREFERRED,
}


class CourseFilter(django_filters.FilterSet):
    """Filters on the creator/admin My Courses screen."""

    course_id = django_filters.UUIDFilter(field_name="id", label="Course ID")
    search = django_filters.CharFilter(method="filter_search", label="Course title")
    creator_type = django_filters.ChoiceFilter(
        field_name="source_type",
        choices=CourseSourceType.choices,
        label="Creator type",
    )
    quality_score = django_filters.NumberFilter(
        field_name="quality_score", lookup_expr="exact"
    )
    date_from = django_filters.DateFilter(
        field_name="created_datetime", lookup_expr="date__gte", label="Start date"
    )
    date_to = django_filters.DateFilter(
        field_name="created_datetime", lookup_expr="date__lte", label="End date"
    )

    class Meta:
        model = Course
        fields = {
            "category": ["exact"],
            "topic": ["exact"],
            "status": ["exact"],
            "source_type": ["exact"],
            "difficulty_level": ["exact"],
        }

    def filter_search(self, queryset, name, value):
        return queryset.filter(title__icontains=value)


class CourseReviewQueueFilter(django_filters.FilterSet):
    """Filters for the reviewer queue. `track` is an explicit ad hoc
    override independent of the reviewer's stored QueueBehaviourPreference -
    see CourseReviewViewSet.get_queryset for how the two interact."""

    track = django_filters.ChoiceFilter(
        choices=QueueTrackFilter.choices,
        method="filter_track",
        label="Track",
    )
    difficulty_level = django_filters.CharFilter(field_name="difficulty_level")
    reviewer = django_filters.UUIDFilter(method="filter_reviewer")
    approved_by = django_filters.UUIDFilter(method="filter_approved_by")
    date_from = django_filters.DateFilter(method="filter_date_from")
    date_to = django_filters.DateFilter(method="filter_date_to")

    class Meta:
        model = Course
        fields = {
            "status": ["exact"],
            "category": ["exact"],
            "source_type": ["exact"],
        }

    def filter_date_from(self, queryset, name, value):
        return queryset.filter(
            Q(submitted_at__date__gte=value)
            | Q(approved_at__date__gte=value)
            | Q(published_at__date__gte=value)
        ).distinct()

    def filter_date_to(self, queryset, name, value):
        return queryset.filter(
            Q(submitted_at__date__lte=value)
            | Q(approved_at__date__lte=value)
            | Q(published_at__date__lte=value)
        ).distinct()

    def filter_reviewer(self, queryset, name, value):
        """Match the assigned reviewer or a reviewer who recorded a decision."""

        return queryset.filter(
            Q(review_assignments__reviewer_id=value)
            | Q(review_actions__reviewer_id=value)
        ).distinct()

    def filter_approved_by(self, queryset, name, value):
        """Match the reviewer behind an approval decision."""

        return queryset.filter(
            review_actions__reviewer_id=value,
            review_actions__action=ReviewActionType.APPROVE,
        ).distinct()

    def filter_track(self, queryset, name, value):
        category_track_preference = _TRACK_FILTER_TO_CATEGORY_TRACK_PREFERENCE.get(
            value
        )
        if category_track_preference is None:
            return queryset
        return queryset.filter(category__track_preference=category_track_preference)


class AdminCourseFilter(CourseFilter):
    """Filters used by the cross-creator Admin Courses table."""

    creator = django_filters.UUIDFilter(field_name="creator_id")
    reviewer = django_filters.UUIDFilter(
        field_name="review_assignments__reviewer_id", distinct=True
    )
    review_stage = django_filters.ChoiceFilter(
        choices=ReviewStage.choices, method="filter_review_stage"
    )

    class Meta(CourseFilter.Meta):
        fields = {
            **CourseFilter.Meta.fields,
            "creator": ["exact"],
        }

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(id__icontains=value)
            | Q(title__icontains=value)
            | Q(creator__first_name__icontains=value)
            | Q(creator__last_name__icontains=value)
            | Q(creator__email__icontains=value)
        )

    def filter_review_stage(self, queryset, name, value):
        if value == ReviewStage.QA:
            return queryset.filter(status="QA_VERIFICATION")
        return queryset.exclude(status="QA_VERIFICATION")
