import django_filters
from django.db.models import Q

from api.collaborators.models import CourseCollaborator, WorkspaceCollaborator


class CollaboratorFilter(django_filters.FilterSet):
    """Filters that mirror the controls on the Collaborators screen."""

    search = django_filters.CharFilter(method="filter_search", label="Name or email")
    date_from = django_filters.DateFilter(
        field_name="created_datetime", lookup_expr="date__gte", label="Date from"
    )
    date_to = django_filters.DateFilter(
        field_name="created_datetime", lookup_expr="date__lte", label="Date to"
    )
    category = django_filters.UUIDFilter(field_name="course__category_id")

    class Meta:
        model = CourseCollaborator
        fields = {"role": ["exact"], "course": ["exact"]}

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(user__first_name__icontains=value)
            | Q(user__last_name__icontains=value)
            | Q(user__email__icontains=value)
        )


class WorkspaceCollaboratorFilter(django_filters.FilterSet):
    """Filters used by the account-level Collaborators Figma screen."""

    search = django_filters.CharFilter(method="filter_search", label="Name or email")
    category = django_filters.UUIDFilter(
        method="filter_category", label="Course category"
    )
    date_from = django_filters.DateFilter(
        field_name="created_datetime", lookup_expr="date__gte", label="Date from"
    )
    date_to = django_filters.DateFilter(
        field_name="created_datetime", lookup_expr="date__lte", label="Date to"
    )

    class Meta:
        model = WorkspaceCollaborator
        fields = {"role": ["exact"], "status": ["exact"]}

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(user__first_name__icontains=value)
            | Q(user__last_name__icontains=value)
            | Q(user__email__icontains=value)
            | Q(invited_email__icontains=value)
        )

    def filter_category(self, queryset, name, value):
        """Match members assigned to an owner-created course in the category."""

        return queryset.filter(
            user__course_collaborations__course__creator=self.request.user,
            user__course_collaborations__course__category_id=value,
        ).distinct()
