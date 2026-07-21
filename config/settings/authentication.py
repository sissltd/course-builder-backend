from decouple import config

EMAIL_TOKEN_BYTES = config("EMAIL_TOKEN_BYTES", default=32, cast=int)
EMAIL_TOKEN_EXPIRY_MINUTES = config("EMAIL_TOKEN_EXPIRY_MINUTES", default=60, cast=int)
EMAIL_TOKEN_MAX_ATTEMPTS = config("EMAIL_TOKEN_MAX_ATTEMPTS", default=5, cast=int)
EMAIL_TOKEN_RESEND_COOLDOWN_SECONDS = config(
    "EMAIL_TOKEN_RESEND_COOLDOWN_SECONDS", default=60, cast=int
)
FRONTEND_URL = config("FRONTEND_URL", default="http://localhost:3000")

WITHDRAWAL_OTP_LENGTH = config("WITHDRAWAL_OTP_LENGTH", default=6, cast=int)
WITHDRAWAL_OTP_EXPIRY_MINUTES = config(
    "WITHDRAWAL_OTP_EXPIRY_MINUTES", default=10, cast=int
)
