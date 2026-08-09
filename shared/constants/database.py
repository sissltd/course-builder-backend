from decouple import config

SECRET_KEY = config("SECRET_KEY")
DATABASE_URL = config("DATABASE_URL")
