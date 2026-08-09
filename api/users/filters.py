import django_filters
from django.db.models import Q

from api.users.models import KYCVerification, User, UserActivityLog


class UserActivityLogFilter(django_filters.FilterSet):
    class Meta:
        model = UserActivityLog
        fields = {
            "category": ["exact"],
            "action": ["exact"],
        }


class KYCReviewQueueFilter(django_filters.FilterSet):
    class Meta:
        model = KYCVerification
        fields = {
            "status": ["exact"],
        }


class UserAdminFilter(django_filters.FilterSet):
    """Filters for the admin user roster.

    `search` matches email or name, so the roster's one search box does not
    need the caller to know which field a term belongs to.
    """

    search = django_filters.CharFilter(
        method="filter_search", label="Email or name contains"
    )

    class Meta:
        model = User
        fields = {
            "role": ["exact"],
            "status": ["exact"],
            "is_active": ["exact"],
        }

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(email__icontains=value)
            | Q(first_name__icontains=value)
            | Q(last_name__icontains=value)
        )


class AdminUserActivityLogFilter(django_filters.FilterSet):
    """Filters for the platform-wide activity log.

    Adds ?user= to UserActivityLogFilter's category/action, which is the whole
    point of the admin view - narrowing the firehose to one account.
    """

    class Meta:
        model = UserActivityLog
        fields = {
            "user": ["exact"],
            "category": ["exact"],
            "action": ["exact"],
        }
