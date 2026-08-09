"""Plain-function test object builders (no factory_boy dependency).

Self-contained (does not import api.courses.tests.factories) to keep
api.categories' test suite independent of unrelated app boundaries, matching
the convention in api.authentication.tests.factories. Categories is a
dependency of courses, not the other way around, so reaching into the courses
factories here would invert that.
"""

from decimal import Decimal
from itertools import count

from django.contrib.auth import get_user_model

from api.categories.enums import CategoryStatus, TrackPreference
from api.categories.models import Category
from api.users.enums import UserRole

User = get_user_model()

_sequence = count(1)


def make_user(role=UserRole.COURSE_CREATOR, **kwargs):
    n = next(_sequence)
    defaults = {
        "email": f"catuser{n}@example.com",
        "first_name": "Test",
        "last_name": "User",
        "role": role,
    }
    defaults.update(kwargs)
    return User.objects.create_user(password="testpass123", **defaults)


def make_category(**kwargs):
    n = next(_sequence)
    defaults = {
        "name": f"Category {n}",
        "description": "A test category.",
        "creator_price": Decimal("100.00"),
        "track_preference": TrackPreference.OPEN,
        "status": CategoryStatus.ACTIVE,
    }
    defaults.update(kwargs)
    return Category.objects.create(**defaults)
