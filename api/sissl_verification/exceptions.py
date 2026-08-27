"""
Domain exceptions for the SISSL verification flow.

These live in a single module (rather than at the top of each service file —
which is the usual Feexeet pattern) because they are shared by all three
SISSL service methods. Views catch them and translate to custom_error_response.

NOTE: There is NO bypass / fallback. If any of these are raised, the user
does NOT proceed — verification is automatic and mandatory.
"""


# >>>>>>>>>>>>>>>>>>>>>>>>> Custom Exceptions <<<<<<<<<<<<<<<<<<<<<<<<
class SISSLError(Exception):
    """
    Generic upstream failure from SISSL.

    Raised when the HTTP call itself failed: timeout, non-2xx response,
    malformed body, vendor maintenance page, or our own rate-limit cap
    being exceeded before the call was made.

    Views should translate this to HTTP 503 with a friendly message
    ('Verification service temporarily unavailable.').
    """


class SISSLLivenessFailed(Exception):
    """
    The selfie was classified as not-real, or its score was below the
    configured liveness threshold.

    This is a CLEAN failure — SISSL responded successfully, the user's
    photo just didn't pass. The user must retake the selfie and try again
    (subject to the per-user hourly cap).

    Views should translate this to HTTP 400 with the exception's message.
    """


class SISSLBVNNotFound(Exception):
    """
    SISSL returned status != 'found' for the supplied BVN.

    The BVN is well-formed but doesn't resolve to an identity at the NIBSS
    backend. Treated as a hard failure — the user does NOT proceed.

    Views should translate this to HTTP 404.
    """


class SISSLNINNotFound(Exception):
    """
    SISSL returned a non-success status for the supplied NIN.

    NIN equivalent of SISSLBVNNotFound. Views should translate this to HTTP 404.
    """
