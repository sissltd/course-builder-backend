"""Guardrails for platform-owned (SYSTEM) MIE developer accounts.

Both apply to SYSTEM accounts only - external developers never meet
either. They live in the environment rather than in code so an incident
can tighten them without a release.
"""

from decouple import config

# Most submissions a SYSTEM account may make in any rolling 24 hours,
# whatever their outcome (dedup short-circuits count). Counted from the
# database, so unlike the per-minute throttle it survives a cache flush.
MIE_SYSTEM_DAILY_SUBMISSION_CAP = config(
    "MIE_SYSTEM_DAILY_SUBMISSION_CAP", default=20, cast=int
)

# Rejection circuit breaker: once a SYSTEM account has at least
# MIE_BREAKER_MIN_DECISIONS admin decisions inside the last
# MIE_BREAKER_WINDOW_DAYS, a rejected share at or above
# MIE_BREAKER_REJECTION_RATE suspends it. The minimum stops two early
# rejections from reading as a 100% failure rate.
MIE_BREAKER_REJECTION_RATE = config(
    "MIE_BREAKER_REJECTION_RATE", default=0.8, cast=float
)
MIE_BREAKER_WINDOW_DAYS = config("MIE_BREAKER_WINDOW_DAYS", default=7, cast=int)
MIE_BREAKER_MIN_DECISIONS = config("MIE_BREAKER_MIN_DECISIONS", default=10, cast=int)

# Studio receipt callbacks (studio.receipt.v1). Receipts are recorded only
# for the Studio developer account, and only while its id is set here; they
# are sent only once the receiver URL, signing secret and CA file are set too.
# Secrets come from the vault, never from code or Git.
STUDIO_RECEIPT_DEVELOPER_ACCOUNT_ID = config(
    "STUDIO_RECEIPT_DEVELOPER_ACCOUNT_ID", default=""
)
STUDIO_RECEIPT_URL = config("STUDIO_RECEIPT_URL", default="")
STUDIO_RECEIPT_ENVIRONMENT = config("STUDIO_RECEIPT_ENVIRONMENT", default="demo")
STUDIO_RECEIPT_KEY_ID = config("STUDIO_RECEIPT_KEY_ID", default="")
STUDIO_RECEIPT_SECRET = config("STUDIO_RECEIPT_SECRET", default="")
# The private CA that signs the receiver's certificate. TLS is always verified.
STUDIO_RECEIPT_CA_FILE = config("STUDIO_RECEIPT_CA_FILE", default="")
# Optional client certificate for mutual TLS.
STUDIO_RECEIPT_CLIENT_CERT = config("STUDIO_RECEIPT_CLIENT_CERT", default="")
STUDIO_RECEIPT_CLIENT_KEY = config("STUDIO_RECEIPT_CLIENT_KEY", default="")
