"""Plain-function test object builders (no factory_boy dependency).

Reuses make_user from api.authentication test factories rather than
duplicating it, unlike authentication's tests which deliberately stay
self-contained.
"""

from api.authentication.tests.factories import make_user  # noqa: F401 - re-exported for convenience
from api.onboarding.models import CreatorProfile


def make_creator_profile(*, user, **kwargs):
    return CreatorProfile.objects.create(user=user, **kwargs)
