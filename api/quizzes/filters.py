import django_filters
from api.quizzes.models import Quiz


class QuizFilter(django_filters.FilterSet):
    class Meta:
        model = Quiz
        fields = {
            "level": ["exact"],
            "passing_score": ["exact"],
        }
