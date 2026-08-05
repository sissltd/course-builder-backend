"""Plain-function test object builder for CourseCollaborator (no factory_boy
dependency), matching the style of api.courses.tests.factories."""

from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import CourseCollaborator
from api.courses.tests.factories import make_draft_course, make_user


def make_collaborator(*, course=None, user=None, **kwargs):
    defaults = {
        "course": course or make_draft_course(),
        "user": user or make_user(),
        "role": CollaboratorRole.COLLABORATOR,
    }
    defaults.update(kwargs)
    return CourseCollaborator.objects.create(**defaults)
