import django_filters

from api.categories.models import Category


class CategoryFilter(django_filters.FilterSet):
    class Meta:
        model = Category
        fields = {
            "track_preference": ["exact"],
            "status": ["exact"],
        }
