import django_filters

from api.users.models import KYCVerification, UserActivityLog


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
