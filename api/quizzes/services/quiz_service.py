from django.db.models import Q, QuerySet

from api.collaborators.services import collaborator_service
from api.quizzes.models import Quiz
from api.users.models import User


def quizzes_accessible_to(*, user: User) -> QuerySet[Quiz]:
    """Return quizzes whose parent course the user owns or collaborates on."""

    courses = collaborator_service.get_courses_accessible_to(user)
    return Quiz.objects.filter(
        Q(course__in=courses)
        | Q(module__course__in=courses)
        | Q(lesson__module__course__in=courses)
    ).distinct()


def user_can_access_parent(*, user: User, parent) -> bool:
    """Check that a quiz parent belongs to a course accessible to the user."""

    course_id = getattr(parent, "course_id", None)
    if course_id is None:
        module = getattr(parent, "module", None)
        course_id = getattr(module, "course_id", None)
    if course_id is None:
        course_id = getattr(parent, "id", None)
    return (
        collaborator_service.get_courses_accessible_to(user)
        .filter(pk=course_id)
        .exists()
    )
