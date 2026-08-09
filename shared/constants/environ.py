from decouple import config

DJANGO_ENV = config("DJANGO_ENV", default="development")
