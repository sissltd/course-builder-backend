import django_filters

from api.courses.models import Course, Topic


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
