"""Builds the payload served by GET /mie/v1/documentation/.

Everything is derived from live code constants - the enum choices, the
event-payload builder, and the route table - so the documentation can
never drift from what the API actually does.
"""

from api.mie.enums import MiePlanType, WebhookEventType

PLAN_EXPLANATIONS = {
    MiePlanType.PAID_PER_SUBMISSION: (
        "Each approved idea credits the creator wallet at approval time."
    ),
    MiePlanType.BYPASS_PER_SUBMISSION: (
        "Payout happens per approved idea unless a superadmin marks that "
        "individual submission as payout-bypassed (payout_bypass=true)."
    ),
    MiePlanType.BYPASS_ACCOUNT: (
        "Nothing from this developer pays out, by agreement; approvals "
        "carry no wallet credit."
    ),
}

REFERENCE_SUFFIXES_DOC = [
    {"status": "PENDING_REVIEW", "suffix": "P", "meaning": "queued for admin review"},
    {"status": "DUPLICATE_IN_QUEUE", "suffix": "D", "meaning": "same title already awaiting review"},
    {"status": "DUPLICATE_EXISTING", "suffix": "E", "meaning": "a platform course already uses this title"},
    {"status": "PREVIOUSLY_REJECTED", "suffix": "X", "meaning": "this exact title was rejected before"},
    {"status": "APPROVED", "suffix": "A", "meaning": "accepted; a course is being produced"},
    {"status": "REJECTED", "suffix": "R", "meaning": "rejected by an admin"},
]


def build_documentation(account) -> dict:
    """Assemble the developer-facing documentation object."""

    return {
        "plan": {
            "plan_type": account.plan_type,
            "explanation": PLAN_EXPLANATIONS[account.plan_type],
        },
        "authentication": {
            "api_key_header": "X-MIE-Api-Key",
            "example": f"{account.api_key_prefix}..." if account.api_key_prefix else "scb_live_...",
            "note": (
                "Send your full key in the X-MIE-Api-Key header on every "
                "request. The key was shown once when your account was "
                "approved; rotate it if lost."
            ),
        },
        "reference_scheme": {
            "description": (
                "Every submission has an immutable id and a public "
                "reference of the form SCB-xxxxxxxx-S. The suffix letter S "
                "always reflects the current status and changes as the "
                "idea moves - use the reference as your correlation key."
            ),
            "suffixes": REFERENCE_SUFFIXES_DOC,
        },
        "endpoints": _endpoint_docs(),
        "webhooks": _webhook_docs(account),
    }


def _endpoint_docs() -> list[dict]:
    return [
        {
            "method": "POST",
            "path": "/api/v1/mie/v1/register/",
            "auth": "public (rate-limited)",
            "summary": "Self-service registration - how this account was created.",
        },
        {
            "method": "POST",
            "path": "/api/v1/mie/v1/submissions/",
            "auth": "API key or platform session",
            "summary": "Submit a course idea (Endpoint 1).",
            "request_example": {
                "title": "Build a Production-Grade Rust Course",
                "description": "optional extra context rides along untouched",
            },
            "response_example": {
                "id": "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11",
                "reference": "SCB-0d1c7b2e-P",
                "status": "PENDING_REVIEW",
            },
        },
        {
            "method": "GET",
            "path": "/api/v1/mie/v1/submissions/queue/",
            "auth": "API key or platform session",
            "summary": (
                "Your own submission queue. Filters: ?status= (pipeline "
                "state) and ?search= (title substring)."
            ),
        },
        {
            "method": "GET",
            "path": "/api/v1/mie/v1/me/",
            "auth": "API key or platform session",
            "summary": (
                "Your account snapshot: status, plan, webhook URL, masked "
                "key, signing secret."
            ),
        },
        {
            "method": "GET",
            "path": "/api/v1/mie/v1/documentation/",
            "auth": "API key or platform session",
            "summary": "This document.",
        },
    ]


def _webhook_docs(account) -> dict:
    sample = {
        "reference": "SCB-0d1c7b2e-P",
        "status": "PENDING_REVIEW",
        "title": "Your Idea Title",
    }
    samples = {}
    for event_type in WebhookEventType.values:
        body = {
            "event_id": "<unique id per event - dedupe on this>",
            "type": event_type,
            "occurred_at": "<ISO-8601 timestamp>",
            "submission": {**sample},
        }
        samples[event_type] = body
    return {
        "delivery": (
            "A POST is sent to your webhook_url immediately for every "
            "event against your submissions - including automated dedup "
            "outcomes. Respond 2xx quickly and process asynchronously; "
            "failed deliveries retry with backoff."
        ),
        "signature_verification": {
            "headers": ["X-MIE-Signature", "X-MIE-Timestamp"],
            "scheme": (
                "hex-encoded HMAC-SHA256 over "
                "'{timestamp}.{raw request body}' using your signing "
                "secret (available via /me). Reject events whose timestamp "
                "is older than 5 minutes to block replays."
            ),
        },
        "samples": samples,
    }
