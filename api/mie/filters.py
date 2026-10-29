import django_filters
from django.db.models import Q

from api.mie.enums import MieSourceType
from api.mie.models import CourseSubmission


class AdminSubmissionFilterSet(django_filters.FilterSet):
    """Full filter surface for the admin queue - the counterpart of the
    developer route, where scoping by developer is the point."""

    developer = django_filters.UUIDFilter(
        field_name="developer__id", help_text="Filter to one developer account id."
    )
    email = django_filters.CharFilter(
        field_name="developer__email",
        lookup_expr="iexact",
        help_text="Filter to one developer account by exact email.",
    )
    source_type = django_filters.ChoiceFilter(
        field_name="developer__source_type",
        choices=MieSourceType.choices,
        help_text=(
            "EXTERNAL for third-party developers, SYSTEM for platform-owned "
            "integrations (the MIE crawler). Matched on the owning account."
        ),
    )
    created_after = django_filters.IsoDateTimeFilter(
        field_name="created_datetime",
        lookup_expr="gte",
        help_text="ISO-8601 lower bound on when the idea arrived.",
    )
    created_before = django_filters.IsoDateTimeFilter(
        field_name="created_datetime",
        lookup_expr="lte",
        help_text="ISO-8601 upper bound on when the idea arrived.",
    )

    class Meta:
        model = CourseSubmission
        fields = ["status", "payout_bypass", "developer", "email", "source_type"]

    @property
    def qs(self):
        queryset = super().qs
        search = self.data.get("search")
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(developer__email__icontains=search)
            )
        return queryset.order_by("-created_datetime")
