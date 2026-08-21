# my_app/sse_utils.py
import json
import logging
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db import close_old_connections

from api.notification.enums import NotificationType
from api.notification.models import Notification
from api.users.models import User
from shared.redis.redis_service import RedisService

logger = logging.getLogger(__name__)


def _load_initial_in_app_notifications(user):
    """Load initial in-app notifications and close thread-local DB connections.

    This function runs inside sync_to_async; explicitly closing connections here
    prevents thread-held test DB sessions from leaking into teardown.
    """

    close_old_connections()
    try:
        return list(
            Notification.objects.filter(receiver=user, type=NotificationType.IN_APP)
            .values(
                "id",
                "title",
                "content",
                "content_type",
                "is_read",
                "created_datetime",
                "metadata",
            )
            .order_by("-created_datetime", "-id")[:20]
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


def get_user_notifications(user, is_read=None):
    """Fetches notifications for a user in a stable, cursor-friendly order.
    Allows filtering by read/unread status."""

    reqs = (
        Notification.objects.filter(receiver=user, type=NotificationType.IN_APP)
        .order_by("-created_datetime", "-id")
        .values(
            "id",
            "title",
            "content",
            "content_type",
            "is_read",
            "created_datetime",
            "metadata",
        )
    )
    if is_read is not None:
        reqs = reqs.filter(is_read=is_read)
    return reqs


def toggle_notification_read_status(notification_id: UUID, user: User, status: bool):
    """Marks a specific notification as read/unread for a user.
    Allowed to work with all types of notifications, for reusability.
    Idempotent: calling it multiple times with the same status has no effect.
    """
    try:
        notification = Notification.objects.get(id=notification_id, receiver=user)
        notification.is_read = status
        notification.save()
        return notification
    except Notification.DoesNotExist:
        logger.warning(f"Notification {notification_id} not found for user {user.id}.")
        raise
