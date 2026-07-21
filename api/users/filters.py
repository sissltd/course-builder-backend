import django_filters

from api.users.models import UserActivityLog


class UserActivityLogFilter(django_filters.FilterSet):
    class Meta:
        model = UserActivityLog
        fields = {
            "category": ["exact"],
            "action": ["exact"],
        }
