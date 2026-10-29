from decouple import Csv, config

from shared.constants.environ import DJANGO_ENV

EMAIL_TOKEN_BYTES = config("EMAIL_TOKEN_BYTES", default=32, cast=int)
EMAIL_TOKEN_EXPIRY_MINUTES = config("EMAIL_TOKEN_EXPIRY_MINUTES", default=60, cast=int)
EMAIL_TOKEN_MAX_ATTEMPTS = config("EMAIL_TOKEN_MAX_ATTEMPTS", default=5, cast=int)
EMAIL_TOKEN_RESEND_COOLDOWN_SECONDS = config(
    "EMAIL_TOKEN_RESEND_COOLDOWN_SECONDS", default=60, cast=int
)
FRONTEND_URL = config("FRONTEND_URL", default="http://localhost:3000")
GOOGLE_OAUTH_CLIENT_IDS = tuple(
    value
    for value in config("GOOGLE_OAUTH_CLIENT_IDS", default="", cast=Csv())
    if value
)

WITHDRAWAL_OTP_LENGTH = config("WITHDRAWAL_OTP_LENGTH", default=6, cast=int)
WITHDRAWAL_OTP_EXPIRY_MINUTES = config(
    "WITHDRAWAL_OTP_EXPIRY_MINUTES", default=10, cast=int
)
# Bootstrap is enabled by code for local/development/staging-like environments
# and disabled for production deployments.
SUPERADMIN_BOOTSTRAP_ENABLED = DJANGO_ENV.lower() in {
    "local",
    "development",
    "dev",
    "staging",
    "pre-production",
}

# MFA is opt-in for every role. This flag only governs the strong-MFA session
# gate (IsStrongMFASession) on money/identity actions, which is inert in
# development/staging-like environments. It never affects login: an account
# that enrolled a device is always challenged, in every environment.
MFA_ENFORCED = DJANGO_ENV.lower() not in {
    "local",
    "development",
    "dev",
    "staging",
    "pre-production",
}
