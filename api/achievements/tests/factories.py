"""Plain-function builders for achievements tests."""

from itertools import count

from django.utils import timezone

from api.achievements.enums import AwardSource, BadgeCriterion
from api.achievements.models import Badge, CreatorBadge

_sequence = count(1)


def make_badge(**kwargs):
    n = next(_sequence)
    defaults = {
        "title": f"Badge {n}",
        "icon": "diamond",
        "color": "#F2994A",
        "criterion": BadgeCriterion.COURSES_CREATED,
        "required_count": n * 100,
        "auto_award": False,
    }
    defaults.update(kwargs)
    return Badge.objects.create(**defaults)


def make_award(*, badge, creator, **kwargs):
    defaults = {
        "badge": badge,
        "creator": creator,
        "source": AwardSource.MANUAL,
        "awarded_at": timezone.now(),
    }
    defaults.update(kwargs)
    return CreatorBadge.objects.create(**defaults)
