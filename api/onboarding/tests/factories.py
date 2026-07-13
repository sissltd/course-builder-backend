"""Plain-function test object builders (no factory_boy dependency).

Reuses make_user/make_category from api.authentication/api.courses test
factories rather than duplicating them - CreatorProfile genuinely depends on
both User and Category at the model level, so this cross-app test reuse
mirrors a real production dependency, unlike authentication's tests which
deliberately stay self-contained.
"""

from api.authentication.tests.factories import make_user  # noqa: F401 - re-exported for convenience
from api.courses.tests.factories import make_category  # noqa: F401 - re-exported for convenience
from api.onboarding.models import CreatorProfile


def make_creator_profile(*, user, **kwargs):
    return CreatorProfile.objects.create(user=user, **kwargs)
