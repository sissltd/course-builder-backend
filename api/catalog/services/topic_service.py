from django.db import transaction

from api.authorization import codenames
from api.authorization.services import permission_service
from api.catalog.models import Topic
from api.catalog.services.category_service import PRICE_FIELDS, require_price_setter
from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.users.models import User


PENDING_REVIEW_STATUSES = [CourseStatus.SUBMITTED, CourseStatus.IN_REVIEW]


def _plain(value):
    """A model instance compares by its key, as `values()` returns it."""

    return getattr(value, "pk", value)


def create_topic(*, actor: User | None, data: dict) -> Topic:
    """Create a topic. Giving it its own prices (rather than its category's)
    needs `catalog.set_creator_prices`."""

    if actor is not None and PRICE_FIELDS & {key for key, value in data.items() if value is not None}:
        require_price_setter(actor)
    return Topic.objects.create(**data, created_by=actor, updated_by=actor)


def update_topic(*, topic: Topic, actor: User | None, data: dict) -> Topic:
    """Update a topic and keep pending review course payouts in sync.

    Only what actually changes is checked: one of its three price overrides
    (null = use the category's) needs `catalog.set_creator_prices`, and
    anything else needs `catalog.manage_topics`. So the Writer may reprice a
    topic, and topic managers may edit the rest, each without the other's
    permission (403 otherwise). Courses lock their prices at submission so approved and
    published payouts stay auditable, but a repricing while courses are
    still in the review queue (and unpaid) re-locks theirs.
    """

    # Compared with the stored row, not the instance: the Django admin has
    # already applied the form's values to it before this runs.
    stored = Topic.objects.filter(pk=topic.pk).values(*data.keys()).first() or {}
    changed = {field for field, value in data.items() if stored.get(field) != _plain(value)}
    reprices = bool(PRICE_FIELDS & changed)
    if actor is not None:
        if reprices:
            require_price_setter(actor)
        if changed - PRICE_FIELDS:
            permission_service.require_permission(actor, codenames.CATALOG_MANAGE_TOPICS)

    with transaction.atomic():
        for field, value in data.items():
            setattr(topic, field, value)

        if actor is not None:
            topic.updated_by = actor
        topic.save()

        if reprices:
            from api.payments.services import course_payout_service

            pending = list(
                Course.objects.filter(
                    topic=topic, status__in=PENDING_REVIEW_STATUSES, creator_paid_at__isnull=True
                ).select_related("topic__category")
            )
            for course in pending:
                course_payout_service.lock_rates(course)
            Course.objects.bulk_update(
                pending, ["creator_rates_snapshot", "payout_type", "creator_price_snapshot"]
            )

    return topic
