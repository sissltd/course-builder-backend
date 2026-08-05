from django.db import transaction

from api.courses.enums import CourseStatus
from api.courses.models import Course, Topic
from api.users.models import User


PENDING_REVIEW_STATUSES = [CourseStatus.SUBMITTED, CourseStatus.IN_REVIEW]


def update_topic(*, topic: Topic, actor: User | None, data: dict) -> Topic:
    """Update a topic and keep pending review course payouts in sync.

    Courses snapshot their payout at submission so approved/published payouts
    stay auditable. A reviewer repricing a topic while courses are still in the
    review queue, however, should affect those unsettled courses.
    """

    new_creator_price = data.get("creator_price")
    should_refresh_pending_snapshots = "creator_price" in data

    with transaction.atomic():
        for field, value in data.items():
            setattr(topic, field, value)

        if actor is not None:
            topic.updated_by = actor
        topic.save()

        if should_refresh_pending_snapshots:
            Course.objects.filter(
                topic=topic,
                status__in=PENDING_REVIEW_STATUSES,
            ).update(creator_price_snapshot=new_creator_price)

    return topic
