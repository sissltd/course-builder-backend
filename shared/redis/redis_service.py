import json
import logging
import secrets
import string
import uuid
from uuid import UUID

import redis.asyncio as aioredis
from django.conf import settings
from django.core.cache import cache
from django_redis import get_redis_connection

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_redis_client():
    return get_redis_connection("default")


def get_async_redis_client():
    redis_url = settings.CACHES["default"].get("LOCATION")
    if not redis_url:
        return None
    return aioredis.Redis.from_url(redis_url)


# -- centralized Key naming schema (to help across the codebase) --

"""
Since redis is a flat key-value store, we will use : as the naming convention to stimulate namespacing (e.g otp:value:user@email.com) - which also makes it readable.
"""


def _otp_key(email):
    return f"otp:value:{email.lower()}"


def _otp_email_rate_key(email):
    return f"rate:otp_req:email:{email.lower()}"


def _otp_ip_rate_key(ip):
    return f"rate:otp_req:ip:{ip}"


def _otp_failed_key(email):
    return f"rate:otp_fail:{email.lower()}"


class RedisService:
    """Centralized Redis service with OTP, rate limiting, and generic operations."""

    @staticmethod
    def get_redis_client():
        """Get the Redis client connection."""
        return get_redis_client()

    @staticmethod
    def get_async_redis_client():
        """Get the asynchronous Redis client connection. Uses the bare-bones
        Python redis client, not django-redis, for async support."""
        return get_async_redis_client()

    @staticmethod
    async def publish_user_notification(user_id: UUID, message: str) -> None:
        """
        Publish a user notification to Redis channel.
        """
        try:
            client = RedisService.get_async_redis_client()
            if client is None:
                logger.debug("Skipping notification publishing because the default cache has no Redis URL")
                return
            channel = f"user:notifications:{user_id}"
            payload = json.dumps({"message": message})
            await client.publish(channel, payload)
            logger.info(
                f"Published notification to user {user_id} on channel {channel}"
            )
        except Exception as e:
            logger.error(f"Error publishing notification for user {user_id}: {e}")

    # --- Generic Redis Operations ---
    @staticmethod
    def set(key: str, value: str, expiry_seconds: int | None = None) -> bool:
        """
        Generic method to store key-value pair in Redis.

        Args:
            key: The key
            value: The value
            expiry_seconds: Optional expiration time in seconds

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            cache.set(key, value, timeout=expiry_seconds)
            logger.info(f"Stored key {key} in Redis")
            return True
        except Exception as e:
            logger.error(f"Error storing in Redis: {e}")
            return False

    @staticmethod
    def get(key: str) -> str | None:
        """
        Generic method to retrieve value from Redis.

        Args:
            key: The key

        Returns:
            str: The value if exists, None otherwise
        """
        try:
            value = cache.get(key)
            if not value:
                return None

            # Handle bytes and str values robustly to avoid UnicodeDecodeError.
            if isinstance(value, bytes):
                try:
                    return value.decode("utf-8")
                except UnicodeDecodeError:
                    logger.warning(
                        f"Redis value for key {key} contains non-UTF8 bytes; returning repr safely"
                    )
                    return value.decode("utf-8", errors="replace")
            if isinstance(value, str):
                return value

            # Fallback for any other type
            return str(value)
        except Exception as e:
            logger.error(f"Error retrieving from Redis: {e}")
            return None

    @staticmethod
    def delete(key: str) -> bool:
        """
        Generic method to delete key from Redis.

        Args:
            key: The key

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            cache.delete(key)
            logger.info(f"Deleted key {key} from Redis")
            return True
        except Exception as e:
            logger.error(f"Error deleting from Redis: {e}")
            return False

    # --- OTP Generation Logic ---
    @staticmethod
    def generate_otp(length=6):
        """
        Generate a secure OTP using secrets module.
        """
        return "".join(secrets.choice(string.digits) for _ in range(length))

    @staticmethod
    def store_otp(email, otp):
        """
        Store OTP with expiry.
        """
        try:
            client = get_redis_client()
            client.setex(_otp_key(email), settings.OTP_TTL_SECONDS, otp)
            logger.info(f"OTP stored for {email}")
        except Exception as e:
            logger.error(f"Error storing OTP for {email}: {e}")
            raise

    @staticmethod
    def get_otp(email):
        """
        Retrieve OTP.
        """
        try:
            client = get_redis_client()
            value = client.get(_otp_key(email))
            if not value:
                return None

            if isinstance(value, bytes):
                try:
                    return value.decode("utf-8")
                except UnicodeDecodeError:
                    logger.warning(
                        f"OTP value for {email} contains non-UTF8 bytes; returning decode replace"
                    )
                    return value.decode("utf-8", errors="replace")

            if isinstance(value, str):
                return value

            return str(value)
        except Exception as e:
            logger.error(f"Error retrieving OTP for {email}: {e}")
            return None

    @staticmethod
    def delete_otp(email):
        """
        Delete OTP.
        """
        try:
            client = get_redis_client()
            client.delete(_otp_key(email))
            logger.info(f"OTP deleted for {email}")
        except Exception as e:
            logger.error(f"Error deleting OTP for {email}: {e}")

    @classmethod
    def store_otp_role(cls, email: str, role: str) -> None:
        """
        Stores the intended role alongside the OTP.
        Uses the same TTL as the OTP so it auto-expires together.
        """
        from django.conf import settings

        key = f"otp:role:{email}"
        get_redis_client().setex(key, settings.OTP_TTL_SECONDS, role)

    @classmethod
    def get_otp_role(cls, email: str) -> str | None:
        """
        Returns the stored intended role for this email, or None if absent/expired.
        """
        key = f"otp:role:{email}"
        value = get_redis_client().get(key)
        return value.decode() if value else None

    @classmethod
    def delete_otp_role(cls, email: str) -> None:
        """
        Cleans up the role key after it has been consumed.
        """
        get_redis_client().delete(f"otp:role:{email}")

    @staticmethod
    def verify_otp(email: str, otp: str) -> bool:
        """
        Verify OTP and delete on success.
        """
        stored_otp = RedisService.get_otp(email)
        if stored_otp and stored_otp == otp:
            RedisService.delete_otp(email)
            logger.info(f"OTP verified for {email}")
            return True
        logger.warning(f"OTP verification failed for {email}")
        return False

    # --- LUA SCRIPT for Atomic Operations ---
    _INCR_WITH_TTL_SCRIPT = """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then
        redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    return count
    """

    @staticmethod
    def _atomic_increment(key, windows_seconds):
        """
        Atomic increment with TTL.
        """
        client = get_redis_client()
        script = client.register_script(RedisService._INCR_WITH_TTL_SCRIPT)
        count = script(keys=[key], args=[windows_seconds])
        ttl = client.ttl(key)
        return int(count), int(ttl)

    # --- Rate Limiting ---
    @staticmethod
    def check_email_rate_limit(email):
        rl = settings.RATE_LIMIT
        count, ttl = RedisService._atomic_increment(
            _otp_email_rate_key(email), rl["EMAIL_WINDOW"]
        )
        if count > rl["EMAIL_MAX"]:
            return True, max(ttl, 0)
        return False, 0

    @staticmethod
    def check_ip_rate_limit(ip):
        rl = settings.RATE_LIMIT
        count, ttl = RedisService._atomic_increment(
            _otp_ip_rate_key(ip), rl["IP_WINDOW"]
        )
        if count > rl["IP_MAX"]:
            return True, max(ttl, 0)
        return False, 0

    @staticmethod
    def check_failed_attempts(email):
        rl = settings.RATE_LIMIT
        client = get_redis_client()
        raw = client.get(_otp_failed_key(email))
        if raw is None:
            return False, 0
        count = int(raw)
        if count >= rl["FAILED_MAX"]:
            ttl = client.ttl(_otp_failed_key(email))
            return True, max(ttl, 0)
        return False, 0

    @staticmethod
    def record_failed_attempt(email):
        rl = settings.RATE_LIMIT
        count, ttl = RedisService._atomic_increment(
            _otp_failed_key(email), rl["FAILED_WINDOW"]
        )
        return count, max(ttl, 0)

    @staticmethod
    def clear_failed_attempts(email):
        try:
            client = get_redis_client()
            client.delete(_otp_failed_key(email))
            logger.info(f"Failed attempts cleared for {email}")
        except Exception as e:
            logger.error(f"Error clearing failed attempts for {email}: {e}")

    @staticmethod
    def health_check() -> bool:
        """
        Health check for Redis connectivity.
        """
        try:
            client = get_redis_client()
            return client.ping()
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False

    # <<<<<< Banks Cache >>>>>
    _BANKS_CACHE_KEY = "cache:paystack:banks"
    _BANKS_CACHE_TTL = 24 * 60 * 60  # 24 hours

    @classmethod
    def get_cached_banks(cls) -> list[dict] | None:
        try:
            value = cache.get(cls._BANKS_CACHE_KEY)
            if not value:
                return None
            raw = value.decode("utf-8") if isinstance(value, bytes) else value
            return json.loads(raw)
        except Exception as e:
            logger.error(f"Error retrieving cached banks: {e}")
            return None

    @classmethod
    def set_cached_banks(cls, data: list[dict]) -> None:
        try:
            cache.set(
                cls._BANKS_CACHE_KEY,
                json.dumps(data),
                timeout=cls._BANKS_CACHE_TTL,
            )
            logger.info("Banks list cached in Redis for 24 hours")
        except Exception as e:
            logger.error(f"Error caching banks: {e}")

    @staticmethod
    def acquire_lock(lock_name: str, expire_seconds: int = 60) -> str | None:
        """
        Acquire a distributed lock using Redis.
        Returns a unique token if the lock is acquired, else None.
        """
        token = str(uuid.uuid4())
        try:
            client = get_redis_client()
            acquired = client.set(lock_name, token, nx=True, ex=expire_seconds)
        except NotImplementedError:
            acquired = cache.add(lock_name, token, timeout=expire_seconds)

        if not acquired:
            return None  # Failed to get the lock

        return token

    # ----Lua script for unlocking the lock key----
    _UNLOCK_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    @staticmethod
    def release_lock(key, lock_value):
        """
        Release the lock if the lock value matches.
        """

        try:
            client = get_redis_client()
            script = client.register_script(RedisService._UNLOCK_SCRIPT)
            return script(keys=[key], args=[lock_value])
        except NotImplementedError:
            if cache.get(key) != lock_value:
                return 0
            return int(cache.delete(key))
