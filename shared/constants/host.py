from decouple import config

ALLOWED_HOSTS = config("ALLOWED_HOSTS", "")

APP_ALLOWED_HOSTS = [host.strip() for host in ALLOWED_HOSTS.split(",") if host.strip()]
