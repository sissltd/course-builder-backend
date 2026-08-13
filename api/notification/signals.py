# my_app/signals.py

from asgiref.sync import async_to_sync
from django.db.models.signals import post_save
from django.dispatch import receiver

from api.notification.enums import NotificationType
from shared.redis.redis_service import RedisService

from .models import Notification


@receiver(post_save, sender=Notification)
def broadcast_notification(sender, instance, created, **kwargs):
    if not created:
        return

    user = instance.receiver
    recent_notifications = (
        Notification.objects.filter(receiver=user, type=NotificationType.IN_APP)
        .values(
            "id", "title", "content", "content_type",
            "is_read", "created_datetime", "metadata"
        )
        .order_by("-created_datetime", "-id")[:20]
    )

    payload = Notification._serialize_for_json(list(recent_notifications))
    async_to_sync(RedisService.publish_user_notification)(user.id, payload)
