from decouple import config

COMPANY_NAME = config("COMPANY_NAME", "CourseBuilder")
SUPPORT_EMAIL = config("SUPPORT_EMAIL", "support@coursebuilder.com")
FRONTEND_URL = config("FRONTEND_URL", "https://coursebuilder.com")

DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", "no_reply@coursebuilder.com")

VERIFICATION_CODE_EXPIRY_SECONDS = config(
    "VERIFICATION_CODE_EXPIRY_SECONDS", 600, cast=int
)
PASSWORD_RESET_CODE_EXPIRY_SECONDS = config(
    "PASSWORD_RESET_CODE_EXPIRY_SECONDS", 600, cast=int
)
STAFF_INVITATION_EXPIRY_HOURS = config("STAFF_INVITATION_EXPIRY_HOURS", 72, cast=int)
