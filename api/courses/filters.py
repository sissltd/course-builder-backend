import django_filters

from api.courses.models import Category, Course, Topic


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


class CourseReviewQueueFilter(django_filters.FilterSet):
    class Meta:
        model = Course
        fields = {
            "status": ["exact"],
            "category": ["exact"],
        }
