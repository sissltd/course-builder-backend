import django_filters
from django.db.models import Q

from api.collaborators.models import CourseCollaborator


class CollaboratorFilter(django_filters.FilterSet):
    """Filters that mirror the controls on the Collaborators screen."""

    search = django_filters.CharFilter(method="filter_search", label="Name or email")
    date_from = django_filters.DateFilter(
        field_name="created_datetime", lookup_expr="date__gte", label="Date from"
    )
    date_to = django_filters.DateFilter(
        field_name="created_datetime", lookup_expr="date__lte", label="Date to"
    )

    class Meta:
        model = CourseCollaborator
        fields = {"role": ["exact"]}

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(user__first_name__icontains=value)
            | Q(user__last_name__icontains=value)
            | Q(user__email__icontains=value)
        )
