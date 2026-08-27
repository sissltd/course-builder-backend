from decouple import config

from shared.constants.environ import DJANGO_ENV

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
# Bootstrap is enabled by code for local/development/staging-like environments
# and disabled for production deployments.
SUPERADMIN_BOOTSTRAP_ENABLED = DJANGO_ENV.lower() in {
    "local",
    "development",
    "dev",
    "staging",
    "pre-production",
}

# Mandated-role MFA (ADMIN/SUPER_ADMIN) is enforced only outside
# development/staging-like environments. In dev and staging a freshly
# bootstrapped super admin must simply log in with email + password - the
# login response carries `mfa_verified=true` so MFA-gated admin endpoints
# stay reachable without any enrollment ceremony.
MFA_ENFORCED = DJANGO_ENV.lower() not in {
    "local",
    "development",
    "dev",
    "staging",
    "pre-production",
}
