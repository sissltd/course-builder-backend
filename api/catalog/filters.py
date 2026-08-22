import django_filters

from api.catalog.models import Category, Topic


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
