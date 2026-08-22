from rest_framework.routers import DefaultRouter

from api.quizzes.views import question_views, quiz_views

router = DefaultRouter()
router.register("quizzes", quiz_views.QuizViewSet, basename="quiz")
router.register("questions", question_views.QuestionViewSet, basename="question")

urlpatterns = router.urls
