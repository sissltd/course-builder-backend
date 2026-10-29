import django_filters
from django.db.models import Case, CharField, IntegerField, Q, Value, When
from django.db.models.functions import Cast
from rest_framework.filters import OrderingFilter

from api.support.enums import SupportRequestStatus, SupportTicketPriority
from api.support.models import SupportRequest


def _declared_order(field: str, values: list[str]) -> Case:
    """Rank a choice field by the order its enum declares the values."""

    return Case(
        *[
            When(**{field: value}, then=Value(index))
            for index, value in enumerate(values)
        ],
        default=Value(len(values)),
        output_field=IntegerField(),
    )


class SupportTicketOrdering(OrderingFilter):
    """`ordering=priority` sorts Low → Medium → High and `ordering=status`
    follows the ticket lifecycle (Open → In-progress → Resolved → Closed),
    instead of alphabetically. Other fields order as usual."""

    RANKS = {
        "priority": _declared_order("priority", SupportTicketPriority.values),
        "status": _declared_order("status", SupportRequestStatus.values),
    }

    def filter_queryset(self, request, queryset, view):
        ordering = self.get_ordering(request, queryset, view)
        if not ordering:
            return queryset
        terms = []
        for term in ordering:
            descending = term.startswith("-")
            name = term.lstrip("-")
            if name in self.RANKS:
                queryset = queryset.annotate(**{f"{name}_rank": self.RANKS[name]})
                name = f"{name}_rank"
            terms.append(f"-{name}" if descending else name)
        return queryset.order_by(*terms)


class SupportTicketFilter(django_filters.FilterSet):
    """Admin Tickets page: 'Search ticket ID, Ref', Status, Priority Level
    and Date. Status/priority are matched case-insensitively."""

    search = django_filters.CharFilter(
        method="filter_search",
        help_text="Any part of the ticket ID, subject, email or submitter name.",
    )
    status = django_filters.CharFilter(
        method="filter_upper",
        help_text=f"One of {', '.join(SupportRequestStatus.values)}.",
    )
    priority = django_filters.CharFilter(
        method="filter_upper",
        help_text=f"One of {', '.join(SupportTicketPriority.values)}.",
    )
    date_from = django_filters.DateFilter(
        field_name="created_datetime",
        lookup_expr="date__gte",
        help_text="Created on or after this date (YYYY-MM-DD).",
    )
    date_to = django_filters.DateFilter(
        field_name="created_datetime",
        lookup_expr="date__lte",
        help_text="Created on or before this date (YYYY-MM-DD).",
    )

    class Meta:
        model = SupportRequest
        fields = ["search", "status", "priority", "date_from", "date_to"]

    def filter_upper(self, queryset, name, value):
        return queryset.filter(**{name: value.upper()})

    def filter_search(self, queryset, name, value):
        return queryset.annotate(id_text=Cast("id", CharField())).filter(
            Q(id_text__icontains=value)
            | Q(title__icontains=value)
            | Q(email__icontains=value)
            | Q(submitted_by__first_name__icontains=value)
            | Q(submitted_by__last_name__icontains=value)
        )
