import django_filters
from django.db.models import Q

from api.catalog.models import Category, Topic, TopicReservationRequest


class CategoryFilter(django_filters.FilterSet):
    class Meta:
        model = Category
        fields = {
            "track_preference": ["exact"],
            "status": ["exact"],
        }


class TopicFilter(django_filters.FilterSet):
    class Meta:
        model = Topic
        fields = {
            "category": ["exact"],
            "status": ["exact"],
        }


class AdminReservationRequestFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")
    requested_by = django_filters.UUIDFilter(field_name="requested_by_id")
    date_from = django_filters.DateFilter(
        field_name="created_datetime", lookup_expr="date__gte"
    )
    date_to = django_filters.DateFilter(
        field_name="created_datetime", lookup_expr="date__lte"
    )

    class Meta:
        model = TopicReservationRequest
        fields = {"status": ["exact"], "category": ["exact"]}

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value)
            | Q(requested_by__first_name__icontains=value)
            | Q(requested_by__last_name__icontains=value)
            | Q(requested_by__email__icontains=value)
        )


class ActiveTopicReservationFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")
    creator = django_filters.UUIDFilter(field_name="reserved_by_id")
    date_from = django_filters.DateFilter(
        field_name="reserved_until", lookup_expr="gte"
    )
    date_to = django_filters.DateFilter(field_name="reserved_until", lookup_expr="lte")

    class Meta:
        model = Topic
        fields = {"category": ["exact"]}

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value)
            | Q(reserved_by__first_name__icontains=value)
            | Q(reserved_by__last_name__icontains=value)
            | Q(reserved_by__email__icontains=value)
        )
