from decouple import config

REDIS_URL = (
    config("VALKEY_URL", default=None)
    or config("REDIS_URL", default=None)
    or "redis://localhost:6379/0"
)
