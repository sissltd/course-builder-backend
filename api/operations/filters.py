"""Filters behind the admin Recommendations screen's toolbar.

Applied by hand in the view rather than through a filter backend, because
the views here are plain APIViews - see the house conventions in
docs/standards/ENGINEERING_STANDARDS.md.
"""

import django_filters

from api.courses.enums import DifficultyLevel
from api.mie.models import CourseSubmission


class MieRecommendationFilterSet(django_filters.FilterSet):
    """Search, category, difficulty, score and date range, as drawn."""

    search = django_filters.CharFilter(
        method="filter_search",
        help_text="Case-insensitive substring match on the idea title.",
    )
    category = django_filters.UUIDFilter(
        field_name="category_id",
        help_text="Platform category id, from GET /api/v1/categories/.",
    )
    difficulty_level = django_filters.ChoiceFilter(
        choices=DifficultyLevel.choices,
        help_text="Difficulty the submitter claimed. An unknown value is a 400.",
    )
    min_demand_score = django_filters.NumberFilter(
        field_name="demand_score",
        lookup_expr="gte",
        help_text=(
            "Keep ideas scored at least this high. Unscored ideas drop out "
            "whenever this is set, since they have no score to compare."
        ),
    )
    submitted_after = django_filters.IsoDateTimeFilter(
        field_name="created_datetime",
        lookup_expr="gte",
        help_text="ISO-8601 lower bound on when the idea arrived.",
    )
    submitted_before = django_filters.IsoDateTimeFilter(
        field_name="created_datetime",
        lookup_expr="lte",
        help_text="ISO-8601 upper bound on when the idea arrived.",
    )

    class Meta:
        model = CourseSubmission
        fields = ["category", "difficulty_level"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(title__icontains=value)
