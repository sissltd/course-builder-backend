# my_app/sse_utils.py
import json
import logging
from collections import defaultdict
from uuid import UUID

from asgiref.sync import async_to_sync, sync_to_async
from django.db import close_old_connections, transaction
from django.db.models import F, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from api.notification.enums import NotificationType
from api.notification.models import Notification
from api.users.models import User
from shared.redis.redis_service import RedisService

logger = logging.getLogger(__name__)

#: How many of a user's newest in-app notifications one stream event carries,
#: both on connect and on every push.
STREAM_HISTORY_SIZE = 20

STREAM_FIELDS = (
    "id",
    "title",
    "content",
    "content_type",
    "is_read",
    "created_datetime",
    "metadata",
)


def _load_initial_in_app_notifications(user):
    """Load initial in-app notifications and close thread-local DB connections.

    This function runs inside sync_to_async; explicitly closing connections here
    prevents thread-held test DB sessions from leaking into teardown.
    """

    close_old_connections()
    try:
        return list(
            Notification.objects.filter(receiver=user, type=NotificationType.IN_APP)
            .values(*STREAM_FIELDS)
            .order_by("-created_datetime", "-id")[:STREAM_HISTORY_SIZE]
        )
    finally:
        close_old_connections()


async def event_stream(user):
    """Listens to Redis for new events and streams them to the user."""

    # 1. Send the initial last 20 notifications immediately when they connect
    # This prevents the user from looking at a blank screen until a NEW notification arrives
    initial_notifications = await sync_to_async(_load_initial_in_app_notifications)(
        user
    )
    initial_payload = Notification._serialize_for_json(initial_notifications)
    yield f"data: {json.dumps(initial_payload)}\n\n"

    # 2. Connect to Redis asynchronously and subscribe to the user's channel
    redis_client = RedisService.get_async_redis_client()
    pubsub = redis_client.pubsub()
    channel_name = f"user:notifications:{user.id}"

    await pubsub.subscribe(channel_name)

    try:
        # 3. Stay in a loop listening for incoming data from the Redis channel
        async for message in pubsub.listen():
            if message["type"] == "message":
                data_string = message["data"].decode("utf-8")
                yield f"data: {data_string}\n\n"
    finally:
        # 4. Clean up the connection if the user closes their tab or browser
        await pubsub.unsubscribe(channel_name)
        await redis_client.close()


def broadcast_to_users(*, user_ids) -> None:
    """Push each user's newest in-app notifications to their open streams.

    The event carries the same list a fresh connection gets first, so the
    client can replace what it shows with each event. One query covers
    every user: a row number per receiver keeps each user's newest
    STREAM_HISTORY_SIZE rows, and the rows are grouped in Python.

    Publishing never raises (RedisService logs and swallows), so a Redis
    outage cannot fail the action that created the notification.
    """

    user_ids = set(user_ids)
    if not user_ids:
        return

    rows = (
        Notification.objects.filter(
            receiver_id__in=user_ids, type=NotificationType.IN_APP
        )
        .annotate(
            position=Window(
                expression=RowNumber(),
                partition_by=[F("receiver_id")],
                order_by=[F("created_datetime").desc(), F("id").desc()],
            )
        )
        .filter(position__lte=STREAM_HISTORY_SIZE)
        .order_by("receiver_id", "position")
        .values("receiver_id", *STREAM_FIELDS)
    )
    by_user = defaultdict(list)
    for row in rows:
        by_user[row.pop("receiver_id")].append(row)

    for user_id in user_ids:
        payload = Notification._serialize_for_json(by_user.get(user_id, []))
        async_to_sync(RedisService.publish_user_notification)(user_id, payload)


def get_user_notifications(user, is_read=None):
    """Fetches notifications for a user in a stable, cursor-friendly order.
    Allows filtering by read/unread status."""

    reqs = (
        Notification.objects.filter(receiver=user, type=NotificationType.IN_APP)
        .order_by("-created_datetime", "-id")
        .values(*STREAM_FIELDS)
    )
    if is_read is not None:
        reqs = reqs.filter(is_read=is_read)
    return reqs


def get_unread_count(*, user: User) -> int:
    """How many of `user`'s in-app notifications are unread."""

    return Notification.objects.filter(
        receiver=user, type=NotificationType.IN_APP, is_read=False
    ).count()


def mark_all_read(*, user: User) -> int:
    """Mark every unread in-app notification of `user` as read.

    One UPDATE, scoped to `user` in the query. Returns how many changed;
    when any did, the user's open streams get the refreshed list after
    commit.
    """

    updated = Notification.objects.filter(
        receiver=user, type=NotificationType.IN_APP, is_read=False
    ).update(is_read=True, updated_datetime=timezone.now())
    if updated:
        transaction.on_commit(lambda: broadcast_to_users(user_ids=[user.pk]))
    return updated


def toggle_notification_read_status(notification_id: UUID, user: User, status: bool):
    """Marks a specific notification as read/unread for a user.
    Allowed to work with all types of notifications, for reusability.
    Idempotent: calling it multiple times with the same status has no effect.
    The user's open streams get the refreshed list after commit.
    """
    try:
        notification = Notification.objects.get(id=notification_id, receiver=user)
        notification.is_read = status
        notification.save()
        transaction.on_commit(lambda: broadcast_to_users(user_ids=[user.pk]))
        return notification
    except Notification.DoesNotExist:
        logger.warning(f"Notification {notification_id} not found for user {user.id}.")
        raise
