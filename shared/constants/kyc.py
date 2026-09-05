from decouple import config

# >>>>>>>>>>>>>>>>>>>>>>>>> SISSL Credentials <<<<<<<<<<<<<<<<<<<<<<<<
SISSL_BASE_URL = config("SISSL_BASE_URL", False)
SISSL_API_TOKEN = config("SISSL_API_TOKEN", False)


# >>>>>>>>>>>>>>>>>>>>>>>>> SISSL Thresholds <<<<<<<<<<<<<<<<<<<<<<<<<
"""
Tunable thresholds with env-var defaults.

These act as fallbacks when the SISSLConfiguration singleton row is missing
or a particular field has not been set by the admin.

Each score is on a 0 - 100 scale (returned by SISSL itself).
"""
SISSL_FACE_MATCH_THRESHOLD = config("SISSL_FACE_MATCH_THRESHOLD", default=80, cast=int)
SISSL_FLAGGING_FLOOR = config("SISSL_FLAGGING_FLOOR", default=60, cast=int)
SISSL_LIVENESS_THRESHOLD = config("SISSL_LIVENESS_THRESHOLD", default=80, cast=int)


# >>>>>>>>>>>>>>>>>>>>>>>>> SISSL HTTP <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
# Read timeout (in seconds) for every SISSL HTTP call — how long we wait for the
# vendor (and NIMC/NIBSS behind it) to answer once connected.
SISSL_HTTP_TIMEOUT = config("SISSL_HTTP_TIMEOUT", default=30, cast=int)

# Connect timeout (in seconds) — how long we wait to establish the TCP/TLS
# connection. Kept short so a dead/unreachable vendor host fails fast instead of
# hanging for the full read timeout.
SISSL_CONNECT_TIMEOUT = config("SISSL_CONNECT_TIMEOUT", default=5, cast=int)

# Transport-level retries for a single SISSL call. Kept low on purpose: the
# request path is synchronous behind a ~100s gateway timeout, so every retry
# eats into that budget. Worst-case wall time must stay well under the gateway
# limit — see _build_session for how read-timeouts and 504s are deliberately
# NOT retried.
SISSL_MAX_RETRIES = config("SISSL_MAX_RETRIES", default=2, cast=int)


# >>>>>>>>>>>>>>>>>>>>>>>>> KYC Review SLA <<<<<<<<<<<<<<<<<<<<<<<<<<<<
# Target turnaround (in hours) for an admin to action a KYC submission. Drives
# the "SLA Audit Rate" stat on the verification dashboard — the % of submissions
# reviewed within this window.
KYC_SLA_HOURS = config("KYC_SLA_HOURS", default=48, cast=int)


# >>>>>>>>>>>>>>>>>>>>>>> Cost Discipline <<<<<<<<<<<<<<<<<<<<<<<<<<<<
"""
Per-user hourly cap on liveness calls.

Every successful or attempted SISSL call is billed, so a buggy client (or
abusive one) hitting /liveness/ in a tight loop is a direct money leak.

BVN and NIN are naturally throttled — a user has only one BVN/NIN — so
only liveness needs an explicit cap here.
"""
SISSL_LIVENESS_HOURLY_CAP = config("SISSL_LIVENESS_HOURLY_CAP", default=5, cast=int)
