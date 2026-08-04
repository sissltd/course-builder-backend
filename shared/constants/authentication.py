from decouple import config

COMPANY_NAME = config("COMPANY_NAME", "Feexeet")
SUPPORT_EMAIL = config("SUPPORT_EMAIL", "support@feexeet.com")
FRONTEND_URL = config("FRONTEND_URL", "https://feexeet.com")

DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", "no_reply@feexeet.com")

VERIFICATION_CODE_EXPIRY_SECONDS = config("VERIFICATION_CODE_EXPIRY_SECONDS", 600, cast=int)
PASSWORD_RESET_CODE_EXPIRY_SECONDS = config("PASSWORD_RESET_CODE_EXPIRY_SECONDS", 600, cast=int)

# How long a team-member invitation's activation link stays valid.
STAFF_INVITATION_EXPIRY_HOURS = config("STAFF_INVITATION_EXPIRY_HOURS", 72, cast=int)
# Frontend route the invitation email points at; the token is appended.
STAFF_INVITATION_ACCEPT_PATH = config("STAFF_INVITATION_ACCEPT_PATH", "/team/accept-invite")
SUPERADMIN_INVITATION_ACCEPT_PATH = config("SUPERADMIN_INVITATION_ACCEPT_PATH", "/auth/admin/accept-invite")
