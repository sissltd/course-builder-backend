from django.contrib import admin

from api.quizzes.models import Quiz, Question, QuestionOption


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "level", "passing_score", "attempts_allowed", "created_datetime")
    list_filter = ("level", "passing_score")
    search_fields = ("title", "description")


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("id", "quiz", "question_text", "question_type", "points", "order")
    list_filter = ("question_type", "quiz__level")
    search_fields = ("question_text",)


@admin.register(QuestionOption)
class QuestionOptionAdmin(admin.ModelAdmin):
    list_display = ("id", "question", "option_text", "is_correct", "order")
    list_filter = ("is_correct",)
    search_fields = ("option_text",)