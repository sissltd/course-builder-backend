from decouple import config

PAYSTACK_SECRET_KEY = config("PAYSTACK_SECRET_KEY", default="")

PAYSTACK_MULTIPLIER = 100
