"""Builds the payload served by GET /mie/v1/documentation/.

This is the single source of truth a developer needs to go from "I have
an API key" to "my integration is live" without talking to us. It is
assembled from live code constants - enum members, the reference-suffix
map, the dedup order, the dispatcher's retry table, the throttle rates in
settings - so it cannot drift from what the API actually does.

Layout of the returned object, in the order a developer reads it:

    meta                 what this document is, when it was generated
    api                  base URL, interactive docs, support contact
    your_account         this caller's live account state
    quickstart           the shortest path to a first successful call
    integration_flow     every stage a submission passes through
    authentication       credentials, headers, failure codes
    reference_scheme     SCB-xxxxxxxx-S and what the suffix means
    submission_lifecycle every status, what enters it, what leaves it
    deduplication        the three ordered checks, in order
    plan_and_payouts     what this account's plan means commercially
    endpoints            every route, with request/response examples
    webhooks             delivery, signing, retries, event catalogue
    errors               the error envelope and the codes it carries
    rate_limits          the live throttle rates
    pagination           the list envelope
    go_live_checklist    what to verify before switching on
    faq                  the questions we actually get asked
"""

from django.conf import settings
from django.utils import timezone

from api.mie.authentication import API_KEY_HEADER
from api.mie.enums import (
    DeveloperAccountStatus,
    MiePlanType,
    SubmissionStatus,
    WebhookDeliveryStatus,
    WebhookEventType,
)
from api.mie.services import webhook_dispatcher
from api.mie.services.key_service import API_KEY_PREFIX
from api.mie.services.reference import REFERENCE_SUFFIXES
from api.mie.services.submission_service import EVENT_TYPE_BY_STATUS
from shared.constants.authentication import SUPPORT_EMAIL

DOCUMENTATION_VERSION = "2.0.0"
"""Bump when the shape of this document changes, not when values change."""

API_ROOT = "/api/v1"

SAMPLE_SUBMISSION_ID = "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11"
SAMPLE_SHORT_ID = SAMPLE_SUBMISSION_ID.replace("-", "")[:8]
SAMPLE_TITLE = "Build a Production-Grade Rust Course"


def _sample_reference(status: SubmissionStatus) -> str:
    """A realistic reference for `status`, built the way the model builds it."""

    return f"SCB-{SAMPLE_SHORT_ID}-{REFERENCE_SUFFIXES[status]}"


# ── Prose keyed by live enum members ─────────────────────────────────
# Every dict below is keyed by an enum member, so adding a member without
# describing it raises a KeyError in the tests rather than silently
# shipping a documentation gap.

PLAN_EXPLANATIONS = {
    MiePlanType.PAID_PER_SUBMISSION: (
        "Every idea an admin approves credits the creator wallet. Nothing "
        "you send is exempt unless your plan is changed."
    ),
    MiePlanType.BYPASS_PER_SUBMISSION: (
        "Approved ideas pay out by default, but a superadmin can mark an "
        "individual submission payout_bypass=true, which excludes just "
        "that idea. You are told the moment it happens via the "
        "SUBMISSION_PAYOUT_BYPASS_UPDATED webhook."
    ),
    MiePlanType.BYPASS_ACCOUNT: (
        "Nothing from this account pays out, by prior agreement. "
        "Approvals still happen and courses are still produced - they "
        "simply carry no wallet credit, and payout_bypass on individual "
        "submissions is irrelevant to you."
    ),
}

ACCOUNT_STATUS_MEANING = {
    DeveloperAccountStatus.PENDING: (
        "Registered and waiting on superadmin review. No API key exists "
        "yet and every authenticated endpoint returns 401."
    ),
    DeveloperAccountStatus.APPROVED: (
        "Live. Your API key authenticates, submissions are accepted, and "
        "webhooks are delivered."
    ),
    DeveloperAccountStatus.REJECTED: (
        "Terminal. Key material was destroyed and pending webhook events "
        "were dropped. A new registration under a different email is "
        "required."
    ),
    DeveloperAccountStatus.SUSPENDED: (
        "Frozen, not deleted. Your key returns 401 with code "
        "'account_suspended' and new events queue up undelivered. A "
        "superadmin can restore you to APPROVED, at which point the "
        "queued events are delivered."
    ),
}

SUBMISSION_STATUS_DOCS = {
    SubmissionStatus.PENDING_REVIEW: {
        "meaning": "Accepted and sitting in the admin review queue.",
        "set_by": "Ingestion, when all three dedup checks pass.",
        "terminal": False,
        "next": ["APPROVED", "REJECTED"],
        "action": (
            "Nothing. Wait for SUBMISSION_APPROVED or SUBMISSION_REJECTED."
        ),
    },
    SubmissionStatus.DUPLICATE_IN_QUEUE: {
        "meaning": (
            "An identical title (case-insensitive) is already awaiting "
            "review - possibly from another developer."
        ),
        "set_by": "Ingestion, dedup check 3.",
        "terminal": True,
        "next": [],
        "action": (
            "This idea will never be reviewed. Resubmit under a "
            "meaningfully different title if you still want it considered."
        ),
    },
    SubmissionStatus.DUPLICATE_EXISTING: {
        "meaning": "A course with this exact title already exists on the platform.",
        "set_by": "Ingestion, dedup check 2.",
        "terminal": True,
        "next": [],
        "action": (
            "This idea will never be reviewed. The topic is already "
            "covered; pick a different angle."
        ),
    },
    SubmissionStatus.PREVIOUSLY_REJECTED: {
        "meaning": (
            "This exact title was rejected before. The new row inherits "
            "the original rejection reason."
        ),
        "set_by": "Ingestion, dedup check 1.",
        "terminal": True,
        "next": [],
        "action": (
            "This idea will never be reviewed. Read the inherited "
            "rejection reason on the queue row before resubmitting "
            "anything similar."
        ),
    },
    SubmissionStatus.APPROVED: {
        "meaning": "An admin accepted the idea; a course can now be produced from it.",
        "set_by": "Superadmin decision.",
        "terminal": False,
        "next": ["REJECTED"],
        "action": (
            "Record the approval. Note this is reversible - a later "
            "SUBMISSION_REJECTED for the same reference supersedes it."
        ),
    },
    SubmissionStatus.REJECTED: {
        "meaning": "An admin declined the idea, with a reason and optional note.",
        "set_by": "Superadmin decision.",
        "terminal": False,
        "next": ["APPROVED"],
        "action": (
            "Read rejection_reason and rejection_note. Note this is "
            "reversible - a later SUBMISSION_APPROVED supersedes it."
        ),
    },
}

WEBHOOK_EVENT_DOCS = {
    WebhookEventType.SUBMISSION_QUEUED: {
        "fires_when": "A new idea passed all dedup checks and entered the review queue.",
        "resulting_status": SubmissionStatus.PENDING_REVIEW,
        "extra_fields": [],
    },
    WebhookEventType.SUBMISSION_DUPLICATE_IN_QUEUE: {
        "fires_when": "Ingestion found the same title already awaiting review.",
        "resulting_status": SubmissionStatus.DUPLICATE_IN_QUEUE,
        "extra_fields": [],
    },
    WebhookEventType.SUBMISSION_DUPLICATE_EXISTING: {
        "fires_when": "Ingestion found a published course with this exact title.",
        "resulting_status": SubmissionStatus.DUPLICATE_EXISTING,
        "extra_fields": [],
    },
    WebhookEventType.SUBMISSION_PREVIOUSLY_REJECTED: {
        "fires_when": "Ingestion found this exact title in the rejected history.",
        "resulting_status": SubmissionStatus.PREVIOUSLY_REJECTED,
        "extra_fields": [],
    },
    WebhookEventType.SUBMISSION_APPROVED: {
        "fires_when": (
            "A superadmin approved the idea. Re-fires on every re-approval, "
            "including after a reversal."
        ),
        "resulting_status": SubmissionStatus.APPROVED,
        "extra_fields": [],
    },
    WebhookEventType.SUBMISSION_REJECTED: {
        "fires_when": (
            "A superadmin rejected the idea, from any prior state including "
            "APPROVED."
        ),
        "resulting_status": SubmissionStatus.REJECTED,
        "extra_fields": ["rejection_reason", "rejection_note"],
    },
    WebhookEventType.SUBMISSION_PAYOUT_BYPASS_UPDATED: {
        "fires_when": (
            "A superadmin toggled payout_bypass on this specific submission. "
            "This is a commercial signal, not a pipeline move - the status "
            "does not change."
        ),
        "resulting_status": None,
        "extra_fields": ["payout_bypass"],
    },
}


# ── Public entry point ───────────────────────────────────────────────


def build_documentation(account, *, request=None) -> dict:
    """Assemble the complete developer-facing documentation object.

    `request`, when supplied, is used only to resolve the absolute base
    URL so copy-pasteable examples point at the environment the caller
    actually reached.
    """

    base_url = _base_url(request)
    return {
        "meta": _meta(),
        "api": _api(base_url),
        "your_account": _your_account(account, base_url),
        "quickstart": _quickstart(account, base_url),
        "integration_flow": _integration_flow(),
        "authentication": _authentication(account),
        "reference_scheme": _reference_scheme(),
        "submission_lifecycle": _submission_lifecycle(),
        "deduplication": _deduplication(),
        "plan_and_payouts": _plan_and_payouts(account),
        "endpoints": _endpoints(base_url),
        "webhooks": _webhooks(account),
        "errors": _errors(),
        "rate_limits": _rate_limits(),
        "pagination": _pagination(),
        "go_live_checklist": _go_live_checklist(),
        "faq": _faq(),
    }


def _base_url(request) -> str:
    if request is None:
        return "https://<your-api-host>"
    return request.build_absolute_uri("/").rstrip("/")


# ── Sections ─────────────────────────────────────────────────────────


def _meta() -> dict:
    return {
        "document": "MIE developer integration reference",
        "documentation_version": DOCUMENTATION_VERSION,
        "generated_at": timezone.now().isoformat(),
        "generated_from": (
            "Live server constants. Every status, event type, suffix, "
            "retry delay and rate limit below is read out of the running "
            "code, not maintained by hand."
        ),
        "audience": (
            "External developers integrating with the MIE (Massive Idea "
            "Engine) course-idea pipeline."
        ),
        "read_this_if": (
            "You want to submit course ideas programmatically and react to "
            "their outcomes without polling us or emailing support."
        ),
    }


def _api(base_url: str) -> dict:
    return {
        "name": settings.SPECTACULAR_SETTINGS["TITLE"],
        "version": settings.SPECTACULAR_SETTINGS["VERSION"],
        "base_url": f"{base_url}{API_ROOT}",
        "interactive_docs": f"{base_url}{API_ROOT}/docs/",
        "openapi_schema": f"{base_url}/api/schema/",
        "content_type": "application/json",
        "support_email": SUPPORT_EMAIL,
        "timestamps": "All timestamps are ISO-8601 with a UTC offset.",
        "identifiers": "All resource ids are UUID v4.",
    }


def _your_account(account, base_url: str) -> dict:
    """The live state of the caller's own account, with what it implies."""

    return {
        "email": account.email,
        "status": account.status,
        "status_meaning": ACCOUNT_STATUS_MEANING[
            DeveloperAccountStatus(account.status)
        ],
        "plan_type": account.plan_type,
        "webhook_url": account.webhook_url,
        "api_key_preview": (
            f"{account.api_key_prefix}..." if account.api_key_prefix else None
        ),
        "api_key_issued_at": _iso(account.api_key_issued_at),
        "api_key_last_used_at": _iso(account.api_key_last_used_at),
        "registered_at": _iso(account.created_datetime),
        "decided_at": _iso(account.decided_at),
        "signing_secret_source": (
            f"GET {base_url}{API_ROOT}/mie/v1/me/ returns your signing "
            "secret in full. It verifies our messages to you; it never "
            "authenticates your requests to us."
        ),
        "how_to_change_webhook_url": (
            "The webhook URL is fixed at registration and is not "
            f"self-service. Email {SUPPORT_EMAIL} to have a superadmin "
            "change it."
        ),
    }


def _quickstart(account, base_url: str) -> dict:
    key_example = (
        f"{account.api_key_prefix}..." if account.api_key_prefix else f"{API_KEY_PREFIX}..."
    )
    return {
        "goal": "One authenticated call, one submission, one verified webhook.",
        "steps": [
            {
                "step": 1,
                "title": "Confirm your credentials work",
                "detail": (
                    "A 200 here means your key is valid and your account is "
                    "APPROVED. Anything else, stop and fix it first."
                ),
                "curl": (
                    f"curl -sS {base_url}{API_ROOT}/mie/v1/me/ \\\n"
                    f'  -H "{API_KEY_HEADER}: {key_example}"'
                ),
            },
            {
                "step": 2,
                "title": "Store your signing secret",
                "detail": (
                    "Copy signing_secret out of the /me response into your "
                    "secret store. You need it to verify every webhook."
                ),
                "curl": None,
            },
            {
                "step": 3,
                "title": "Submit your first idea",
                "detail": (
                    "Only `title` is required. Any other keys you send are "
                    "stored verbatim and shown to reviewers. Read `status` "
                    "on the response - a 201 does NOT mean queued."
                ),
                "curl": (
                    f"curl -sS -X POST {base_url}{API_ROOT}/mie/v1/submissions/ \\\n"
                    f'  -H "{API_KEY_HEADER}: {key_example}" \\\n'
                    '  -H "Content-Type: application/json" \\\n'
                    f'  -d \'{{"title": "{SAMPLE_TITLE}", '
                    '"description": "Systems programming for backend engineers"}\''
                ),
            },
            {
                "step": 4,
                "title": "Verify the webhook you just received",
                "detail": (
                    "A signed POST lands on your webhook_url within a "
                    "minute. Recompute the HMAC before trusting it - see "
                    "the `webhooks.verification` section."
                ),
                "curl": None,
            },
            {
                "step": 5,
                "title": "Reconcile with your queue",
                "detail": (
                    "Your queue is the authority on current state. Use it "
                    "to backfill anything your webhook endpoint missed "
                    "while it was down."
                ),
                "curl": (
                    f"curl -sS '{base_url}{API_ROOT}/mie/v1/submissions/queue/"
                    "?status=PENDING_REVIEW' \\\n"
                    f'  -H "{API_KEY_HEADER}: {key_example}"'
                ),
            },
        ],
        "common_first_mistakes": [
            "Treating HTTP 201 as 'queued'. It means 'received and "
            "classified' - read the `status` field for the real outcome.",
            "Putting the API key in an Authorization header. It goes in "
            f"{API_KEY_HEADER}.",
            "Verifying the webhook signature against re-serialized JSON. "
            "Sign the raw request bytes, byte for byte.",
            "Assuming APPROVED and REJECTED are final. Both are reversible.",
        ],
    }


def _integration_flow() -> list[dict]:
    """Every stage from registration to payout, in the order it happens."""

    return [
        {
            "stage": 1,
            "name": "Registration",
            "actor": "You",
            "what_happens": (
                "You POST an email and an HTTPS webhook URL to the public "
                "registration route. An account row is created in PENDING."
            ),
            "your_move": "Register once, then wait.",
            "you_can_authenticate": False,
            "webhook_fired": None,
        },
        {
            "stage": 2,
            "name": "Superadmin approval",
            "actor": "Platform superadmin",
            "what_happens": (
                "A superadmin reviews the registration and approves it. "
                "That moment generates your API key and your webhook "
                "signing secret. The full key is displayed exactly once, "
                "in the approval response, and is never recoverable - only "
                "its SHA-256 hash is stored."
            ),
            "your_move": (
                "Collect the key out-of-band and put it straight into a "
                "secret store."
            ),
            "you_can_authenticate": True,
            "webhook_fired": None,
        },
        {
            "stage": 3,
            "name": "Submission",
            "actor": "You",
            "what_happens": (
                "You POST a course idea. The body is stored verbatim; the "
                "title is extracted as the dedup and indexing key."
            ),
            "your_move": "Send the idea; persist the returned reference.",
            "you_can_authenticate": True,
            "webhook_fired": None,
        },
        {
            "stage": 4,
            "name": "Deduplication",
            "actor": "Platform (automatic, synchronous)",
            "what_happens": (
                "Three ordered checks run inside the same request. The "
                "first one that matches decides the outcome and the rest "
                "are skipped. A match short-circuits the idea out of the "
                "pipeline - it never reaches a human."
            ),
            "your_move": (
                "Branch on the `status` in the 201 response. Three of the "
                "four possible values are dead ends."
            ),
            "you_can_authenticate": True,
            "webhook_fired": (
                "One of SUBMISSION_QUEUED, SUBMISSION_DUPLICATE_IN_QUEUE, "
                "SUBMISSION_DUPLICATE_EXISTING, SUBMISSION_PREVIOUSLY_REJECTED"
            ),
        },
        {
            "stage": 5,
            "name": "Admin review",
            "actor": "Platform superadmin",
            "what_happens": (
                "Ideas that reached PENDING_REVIEW sit in a cross-developer "
                "queue. Admins may also record advisory demand signals "
                "(demand score, estimated monthly earnings) to prioritise "
                "the queue - those are internal and fire no webhook."
            ),
            "your_move": (
                "Nothing. There is no SLA endpoint and no way to expedite; "
                "wait for the decision webhook."
            ),
            "you_can_authenticate": True,
            "webhook_fired": None,
        },
        {
            "stage": 6,
            "name": "Decision",
            "actor": "Platform superadmin",
            "what_happens": (
                "The idea is approved or rejected. Rejection always carries "
                "a reason label from a managed taxonomy, plus an optional "
                "free-text note. Decisions are reversible from any state "
                "and every flip re-fires the matching event."
            ),
            "your_move": (
                "Handle SUBMISSION_APPROVED / SUBMISSION_REJECTED. Treat "
                "the newest event for a reference as current - never assume "
                "the first decision is the last."
            ),
            "you_can_authenticate": True,
            "webhook_fired": "SUBMISSION_APPROVED or SUBMISSION_REJECTED",
        },
        {
            "stage": 7,
            "name": "Production",
            "actor": "Platform",
            "what_happens": (
                "An approved idea becomes a course produced by the "
                "platform. If that course is later reversed by a rejection "
                "it is unpublished and parked for review, never deleted, so "
                "a subsequent re-approval relinks the same course instead "
                "of duplicating it."
            ),
            "your_move": (
                "Nothing. Course production is not exposed on the developer "
                "API surface."
            ),
            "you_can_authenticate": True,
            "webhook_fired": None,
        },
        {
            "stage": 8,
            "name": "Payout",
            "actor": "Platform",
            "what_happens": (
                "Whether an approval carries wallet credit is governed by "
                "your account plan_type and, where the plan allows it, the "
                "per-submission payout_bypass flag. See plan_and_payouts."
            ),
            "your_move": (
                "Handle SUBMISSION_PAYOUT_BYPASS_UPDATED if your plan is "
                "BYPASS_PER_SUBMISSION."
            ),
            "you_can_authenticate": True,
            "webhook_fired": "SUBMISSION_PAYOUT_BYPASS_UPDATED (when toggled)",
        },
    ]


def _authentication(account) -> dict:
    key_example = (
        f"{account.api_key_prefix}..." if account.api_key_prefix else f"{API_KEY_PREFIX}..."
    )
    return {
        "primary": {
            "type": "API key",
            "header": API_KEY_HEADER,
            "key_format": (
                f"'{API_KEY_PREFIX}' followed by 43 url-safe base64 "
                "characters (256 bits of entropy)."
            ),
            "example_header": f"{API_KEY_HEADER}: {key_example}",
            "applies_to": "Every /mie/v1/ route except registration.",
        },
        "alternate": {
            "type": "Bearer session token",
            "header": "Authorization: Bearer <token>",
            "note": (
                "The same developer routes also accept a short-lived "
                "platform session token, used by our own first-party "
                "frontend. There is currently no public endpoint that "
                "mints one, so external integrations use the API key."
            ),
        },
        "precedence": (
            f"If {API_KEY_HEADER} is present it is used and the "
            "Authorization header is ignored. Send exactly one."
        ),
        "storage": {
            "we_store": "Only a SHA-256 hash of your key, plus a 16-character non-secret prefix used for lookup.",
            "we_cannot": "Recover, re-display, or email you the key. It exists in plaintext exactly once, in the approval response.",
        },
        "rotation": (
            f"There is no self-service rotation endpoint. Email {SUPPORT_EMAIL} "
            "to have a superadmin reject and re-approve the account, which "
            "revokes the old key and issues a fresh one. Rejection also "
            "drops undelivered webhook events, so plan a window for it."
        ),
        "failure_codes": [
            {
                "code": "no_credentials",
                "http_status": 401,
                "meaning": f"Neither {API_KEY_HEADER} nor a Bearer token was sent.",
                "fix": "Add the header.",
            },
            {
                "code": "invalid_api_key",
                "http_status": 401,
                "meaning": (
                    f"The key is malformed (missing the '{API_KEY_PREFIX}' "
                    "prefix), unknown, or does not match the stored hash."
                ),
                "fix": "Check for truncation or whitespace. If it is genuinely lost, it cannot be recovered - request re-issuance.",
            },
            {
                "code": "account_suspended",
                "http_status": 401,
                "meaning": "Your key is valid but the account is frozen.",
                "fix": f"Contact {SUPPORT_EMAIL}. Your queue and history are intact; nothing was deleted.",
            },
            {
                "code": "account_not_active",
                "http_status": 401,
                "meaning": "The account is PENDING or REJECTED and holds no active credentials.",
                "fix": "PENDING means approval has not happened yet. REJECTED is terminal.",
            },
            {
                "code": "token_expired",
                "http_status": 401,
                "meaning": "A Bearer session token has passed its expiry.",
                "fix": "Sign in again. Does not apply to API keys, which do not expire.",
            },
        ],
        "hardening": [
            "Send the key over TLS only - it is a bearer credential.",
            "Never put it in a query string, a URL, or client-side code.",
            "Compare nothing yourself; we do the constant-time comparison.",
            "api_key_last_used_at on /me is your cheapest leak detector. An unexpected timestamp means someone else has your key.",
        ],
    }


def _reference_scheme() -> dict:
    return {
        "format": "SCB-<8 hex chars>-<status letter>",
        "example": _sample_reference(SubmissionStatus.PENDING_REVIEW),
        "derivation": (
            "The 8 hex characters are the first 8 of the submission's UUID "
            "with dashes removed. The trailing letter is derived from the "
            "current status every time the reference is rendered."
        ),
        "critical_warning": (
            "The reference is NOT stable. The suffix letter changes as the "
            "submission moves, so the same idea is SCB-0d1c7b2e-P today and "
            "SCB-0d1c7b2e-A tomorrow. Key your database on the immutable "
            "`id` (UUID), or on the SCB-<8 hex> stem - never on the full "
            "reference string."
        ),
        "correlation_advice": (
            "Every webhook carries the reference for the state it "
            "announces. Strip the suffix to correlate, and read the "
            "explicit `status` field for the state."
        ),
        "suffixes": [
            {
                "status": status.value,
                "suffix": suffix,
                "meaning": SUBMISSION_STATUS_DOCS[status]["meaning"],
                "example": f"SCB-{SAMPLE_SHORT_ID}-{suffix}",
            }
            for status, suffix in REFERENCE_SUFFIXES.items()
        ],
    }


def _submission_lifecycle() -> dict:
    return {
        "summary": (
            "A submission lands in exactly one of four states at ingestion. "
            "Three of them are dead ends. Only PENDING_REVIEW continues to "
            "a human decision."
        ),
        "reversibility": (
            "APPROVED and REJECTED are not terminal. A superadmin can flip "
            "either direction at any time, any number of times, and each "
            "flip re-fires the matching webhook. Always treat the most "
            "recent event for a submission as the truth."
        ),
        "statuses": [
            {
                "status": status.value,
                "label": status.label,
                "reference_suffix": REFERENCE_SUFFIXES[status],
                "webhook_event": EVENT_TYPE_BY_STATUS[status].value,
                **docs,
            }
            for status, docs in SUBMISSION_STATUS_DOCS.items()
        ],
        "ingestion_outcomes": [
            SubmissionStatus.PENDING_REVIEW.value,
            SubmissionStatus.DUPLICATE_IN_QUEUE.value,
            SubmissionStatus.DUPLICATE_EXISTING.value,
            SubmissionStatus.PREVIOUSLY_REJECTED.value,
        ],
        "decision_outcomes": [
            SubmissionStatus.APPROVED.value,
            SubmissionStatus.REJECTED.value,
        ],
        "race_condition_note": (
            "A database constraint permits at most one PENDING_REVIEW row "
            "per title platform-wide. If two developers submit the same "
            "title in the same instant, one wins the race and the other is "
            "recorded as DUPLICATE_IN_QUEUE. This is expected, not an error."
        ),
    }


def _deduplication() -> dict:
    return {
        "when": (
            "Synchronously, inside the POST that creates the submission. "
            "The outcome is already in the 201 response."
        ),
        "matching": (
            "Case-insensitive exact match on the whole title, after "
            "trimming leading and trailing whitespace. There is no fuzzy "
            "matching, no stemming, and no substring matching - "
            "'Rust Basics' and 'Rust  Basics' are different titles."
        ),
        "order_matters": (
            "The checks run in the order below and the first match wins. "
            "The remaining checks are skipped, so a title that would match "
            "two checks reports only the first."
        ),
        "checks": [
            {
                "order": 1,
                "name": "Previously rejected",
                "question": "Has this exact title ever been rejected by an admin?",
                "scope": "Platform-wide, across all developers, all history.",
                "outcome": SubmissionStatus.PREVIOUSLY_REJECTED.value,
                "side_effect": (
                    "The new submission inherits the original rejection "
                    "reason, so you can see why it was turned down before."
                ),
            },
            {
                "order": 2,
                "name": "Existing course",
                "question": "Does a course with this exact title already exist?",
                "scope": "The platform's live course catalogue.",
                "outcome": SubmissionStatus.DUPLICATE_EXISTING.value,
                "side_effect": None,
            },
            {
                "order": 3,
                "name": "Already queued",
                "question": "Is this exact title already awaiting review?",
                "scope": "All PENDING_REVIEW submissions, from any developer.",
                "outcome": SubmissionStatus.DUPLICATE_IN_QUEUE.value,
                "side_effect": None,
            },
        ],
        "no_match": (
            f"All three miss -> the submission is stored as "
            f"{SubmissionStatus.PENDING_REVIEW.value} and enters the "
            "review queue."
        ),
        "not_idempotent": (
            "Resubmitting the same title can produce a different outcome "
            "than last time, because the queue and the catalogue move "
            "underneath you. Each POST creates a new submission row - "
            "there is no request-level idempotency key. De-duplicate on "
            "your side before sending if you retry."
        ),
    }


def _plan_and_payouts(account) -> dict:
    plan = MiePlanType(account.plan_type)
    return {
        "your_plan": plan.value,
        "your_plan_label": plan.label,
        "what_it_means_for_you": PLAN_EXPLANATIONS[plan],
        "payout_bypass_applies_to_you": plan == MiePlanType.BYPASS_PER_SUBMISSION,
        "all_plans": [
            {
                "plan_type": member.value,
                "label": member.label,
                "explanation": PLAN_EXPLANATIONS[member],
            }
            for member in MiePlanType
        ],
        "payout_bypass": {
            "field": "payout_bypass",
            "where": "On each submission, visible in your queue and on the bypass webhook.",
            "meaning": (
                "True means the creator will not be paid for this specific "
                "idea. It is a commercial marker set by a superadmin; it "
                "does not change the submission's pipeline status and does "
                "not stop the course being produced."
            ),
            "notification": (
                f"Every toggle fires "
                f"{WebhookEventType.SUBMISSION_PAYOUT_BYPASS_UPDATED.value} "
                "immediately, in both directions."
            ),
        },
        "plan_changes": (
            "A superadmin can change your plan_type at any time. There is "
            "no webhook for it - re-read /me or this document to see the "
            "current plan."
        ),
        "timing": (
            "Payout settlement is a platform-side wallet concern and is "
            "not exposed on the developer API. Approval is the trigger; "
            "the credit itself is not something you can query here."
        ),
    }


def _endpoints(base_url: str) -> list[dict]:
    """Every route a developer can reach, in the order they will use them."""

    prefix = f"{base_url}{API_ROOT}"
    return [
        {
            "name": "Register",
            "method": "POST",
            "path": f"{API_ROOT}/mie/v1/register/",
            "url": f"{prefix}/mie/v1/register/",
            "auth": "None (public)",
            "rate_limit": _rate("mie_register"),
            "purpose": (
                "Self-service registration. This is how your account was "
                "created; you will not call it again."
            ),
            "request_body": {
                "email": "string, required, unique across all accounts",
                "webhook_url": "string, required, HTTPS URL that will receive signed events",
                "plan_type": (
                    "string, optional, one of "
                    f"{[member.value for member in MiePlanType]}; "
                    f"defaults to {MiePlanType.PAID_PER_SUBMISSION.value}. "
                    "A superadmin may override it at approval."
                ),
            },
            "request_example": {
                "email": "dev@studio.io",
                "webhook_url": "https://hooks.studio.io/mie",
                "plan_type": MiePlanType.PAID_PER_SUBMISSION.value,
            },
            "success_status": 201,
            "response_example": {
                "id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
                "email": "dev@studio.io",
                "webhook_url": "https://hooks.studio.io/mie",
                "status": DeveloperAccountStatus.PENDING.value,
                "plan_type": MiePlanType.PAID_PER_SUBMISSION.value,
                "api_key_preview": None,
                "api_key_issued_at": None,
                "api_key_last_used_at": None,
                "decided_at": None,
                "created_datetime": "2026-08-23T08:55:00Z",
                "updated_datetime": "2026-08-23T08:55:00Z",
            },
            "errors": [
                {"status": 400, "when": "Email already registered, or a field is missing or malformed."},
                {"status": 429, "when": "Registration rate limit exceeded for your IP."},
            ],
            "notes": [
                "The response contains no credentials - the account is PENDING and authenticates nothing.",
                "Approval happens out-of-band. Watch your inbox, not your webhook: no event fires for approval.",
            ],
        },
        {
            "name": "Submit a course idea",
            "method": "POST",
            "path": f"{API_ROOT}/mie/v1/submissions/",
            "url": f"{prefix}/mie/v1/submissions/",
            "auth": f"{API_KEY_HEADER} (or Bearer session token)",
            "rate_limit": _rate("mie_ingest"),
            "purpose": "The core endpoint. Submit one course idea into the pipeline.",
            "request_body": {
                "title": (
                    "string, required, 1-255 characters after trimming. The "
                    "sole dedup key."
                ),
                "<anything else>": (
                    "Optional. The entire JSON body is stored verbatim and "
                    "shown to reviewers - description, audience, outline, "
                    "your own internal ids, whatever helps the review."
                ),
            },
            "request_example": {
                "title": SAMPLE_TITLE,
                "description": "Systems programming for backend engineers",
                "audience": "mid-level backend developers",
                "your_internal_id": "idea-4417",
            },
            "success_status": 201,
            "response_example": {
                "id": SAMPLE_SUBMISSION_ID,
                "reference": _sample_reference(SubmissionStatus.PENDING_REVIEW),
                "status": SubmissionStatus.PENDING_REVIEW.value,
                "created_datetime": "2026-08-23T08:55:00Z",
            },
            "response_fields": {
                "id": "Immutable UUID. Key your records on this.",
                "reference": "Public reference; the suffix letter mutates with status.",
                "status": (
                    "The dedup outcome, already decided. One of "
                    f"{[s.value for s in (SubmissionStatus.PENDING_REVIEW, SubmissionStatus.DUPLICATE_IN_QUEUE, SubmissionStatus.DUPLICATE_EXISTING, SubmissionStatus.PREVIOUSLY_REJECTED)]}."
                ),
                "created_datetime": "When we received it.",
            },
            "errors": [
                {"status": 400, "when": "Missing title, empty title, title over 255 characters, or a non-object body."},
                {"status": 401, "when": "Missing, invalid, suspended, or inactive credentials."},
                {"status": 429, "when": "Ingest rate limit exceeded."},
            ],
            "notes": [
                "201 means 'received and classified', NOT 'queued'. Always branch on `status`.",
                "A webhook for the outcome is recorded before the response is returned, and dispatched within a minute.",
                "Every POST creates a new row. There is no idempotency key - retrying a timed-out request may create a duplicate submission that then dedups against the first.",
            ],
        },
        {
            "name": "List your submissions",
            "method": "GET",
            "path": f"{API_ROOT}/mie/v1/submissions/queue/",
            "url": f"{prefix}/mie/v1/submissions/queue/",
            "auth": f"{API_KEY_HEADER} (or Bearer session token)",
            "rate_limit": "None",
            "purpose": (
                "Your own queue, newest first. The authoritative view of "
                "current state, and your recovery path when webhooks are "
                "missed."
            ),
            "query_parameters": [
                {
                    "name": "status",
                    "type": "string",
                    "enum": SubmissionStatus.values,
                    "description": "Restrict to one pipeline state.",
                },
                {
                    "name": "search",
                    "type": "string",
                    "description": "Case-insensitive substring match on the title.",
                },
                {
                    "name": "ordering",
                    "type": "string",
                    "description": "Any model field, prefix with '-' to reverse. Defaults to -created_datetime.",
                },
                {
                    "name": "page",
                    "type": "integer",
                    "description": "1-based page number.",
                },
                {
                    "name": "size",
                    "type": "integer",
                    "description": f"Rows per page. Defaults to {settings.REST_FRAMEWORK['PAGE_SIZE']}.",
                },
            ],
            "success_status": 200,
            "response_example": {
                "status": True,
                "message": "Successfully retrieved data",
                "data": {
                    "paginator": {
                        "count": 1,
                        "page": 1,
                        "page_size": settings.REST_FRAMEWORK["PAGE_SIZE"],
                        "total_pages": 1,
                        "next": None,
                        "next_page_number": None,
                        "previous": None,
                        "previous_page_number": None,
                    },
                    "results": [
                        {
                            "id": SAMPLE_SUBMISSION_ID,
                            "reference": _sample_reference(SubmissionStatus.APPROVED),
                            "title": SAMPLE_TITLE,
                            "status": SubmissionStatus.APPROVED.value,
                            "rejection_reason": None,
                            "payout_bypass": False,
                            "queued_at": "2026-08-23T09:00:00Z",
                            "decided_at": "2026-08-24T15:30:00Z",
                            "created_datetime": "2026-08-23T08:55:00Z",
                        }
                    ],
                },
            },
            "errors": [
                {"status": 401, "when": "Missing, invalid, suspended, or inactive credentials."},
            ],
            "notes": [
                "Scoping is server-side. No query parameter can widen this beyond your own submissions.",
                "Every state appears here, including the three dedup dead ends.",
                "This is the reconciliation surface: if your webhook endpoint was down, replay from here rather than asking us to resend.",
            ],
        },
        {
            "name": "Your account",
            "method": "GET",
            "path": f"{API_ROOT}/mie/v1/me/",
            "url": f"{prefix}/mie/v1/me/",
            "auth": f"{API_KEY_HEADER} (or Bearer session token)",
            "rate_limit": "None",
            "purpose": (
                "Account snapshot and credentials health check. The only "
                "place to retrieve your webhook signing secret."
            ),
            "success_status": 200,
            "response_example": {
                "email": "dev@studio.io",
                "status": DeveloperAccountStatus.APPROVED.value,
                "plan_type": MiePlanType.PAID_PER_SUBMISSION.value,
                "webhook_url": "https://hooks.studio.io/mie",
                "api_key_preview": f"{API_KEY_PREFIX}a1b2c3d...",
                "api_key_last_used_at": "2026-08-24T15:30:00Z",
                "signing_secret": "<your 43-character signing secret>",
                "created_datetime": "2026-08-20T10:00:00Z",
                "decided_at": "2026-08-21T09:00:00Z",
            },
            "errors": [
                {"status": 401, "when": "Missing, invalid, suspended, or inactive credentials."},
            ],
            "notes": [
                "The API key is always masked here. The full key was shown once, at approval.",
                "The signing secret IS returned in full - it only verifies our messages to you and cannot authenticate anything on your behalf.",
                "A 200 from this endpoint is the cheapest possible credentials check.",
            ],
        },
        {
            "name": "This documentation (JSON)",
            "method": "GET",
            "path": f"{API_ROOT}/mie/v1/documentation/",
            "url": f"{prefix}/mie/v1/documentation/",
            "auth": f"{API_KEY_HEADER} (or Bearer session token)",
            "rate_limit": "None",
            "purpose": (
                "This document, generated from live server constants and "
                "personalised to your account."
            ),
            "success_status": 200,
            "errors": [
                {"status": 401, "when": "Missing, invalid, suspended, or inactive credentials."},
            ],
            "notes": [
                "Read-only and idempotent apart from the generated_at timestamp.",
                "Safe to fetch at build time to generate client constants.",
            ],
        },
        {
            "name": "This documentation (PDF)",
            "method": "GET",
            "path": f"{API_ROOT}/mie/v1/documentation/download/",
            "url": f"{prefix}/mie/v1/documentation/download/",
            "auth": f"{API_KEY_HEADER} (or Bearer session token)",
            "rate_limit": "None",
            "purpose": (
                "The same content as a formatted PDF, for circulating "
                "internally or reading away from Swagger."
            ),
            "success_status": 200,
            "response_content_type": "application/pdf",
            "errors": [
                {"status": 401, "when": "Missing, invalid, suspended, or inactive credentials."},
            ],
            "notes": [
                "Returned as an attachment. Content is identical to the JSON document.",
            ],
        },
    ]


def _webhooks(account) -> dict:
    return {
        "why": (
            "Webhooks are the only push channel. There is no polling "
            "endpoint for decisions - if you do not consume webhooks you "
            "will only learn outcomes by re-reading your queue."
        ),
        "your_endpoint": account.webhook_url,
        "delivery": {
            "method": "POST",
            "content_type": "application/json",
            "cadence": (
                "Events are recorded synchronously the instant a transition "
                "happens, and a dispatcher sweeps and sends them once a "
                "minute. Expect delivery within ~60 seconds of the event."
            ),
            "ordering": (
                "Not guaranteed. Deliveries run concurrently, so events can "
                "arrive out of order. Use `occurred_at` to order them, and "
                "ignore an event older than one you have already applied "
                "for the same submission."
            ),
            "expected_response": (
                "Any 2xx. Respond fast and process asynchronously - the "
                "read timeout is "
                f"{webhook_dispatcher.READ_TIMEOUT_SECONDS} seconds "
                f"(connect timeout {webhook_dispatcher.CONNECT_TIMEOUT_SECONDS}s). "
                "A slow 200 is treated as a failure and retried."
            ),
            "non_2xx": "Recorded as a failed attempt and retried on the schedule below.",
            "concurrency": f"Up to {webhook_dispatcher.MAX_WORKERS} deliveries in flight per sweep.",
        },
        "headers": [
            {
                "header": "Content-Type",
                "value": "application/json",
                "purpose": "Body encoding.",
            },
            {
                "header": "X-MIE-Timestamp",
                "value": "Unix epoch seconds at send time",
                "purpose": "Signed alongside the body; also your replay guard.",
            },
            {
                "header": "X-MIE-Signature",
                "value": "Lowercase hex HMAC-SHA256",
                "purpose": "Proves the body came from us and was not altered.",
            },
        ],
        "envelope": {
            "description": (
                "Every event, without exception, has these four top-level "
                "keys. Keys are serialized in sorted order with no "
                "whitespace - do not rely on that, but it is why "
                "re-serializing breaks signature checks."
            ),
            "fields": {
                "event_id": "UUID, unique per event. Your idempotency key.",
                "type": f"One of {WebhookEventType.values}.",
                "occurred_at": "ISO-8601 timestamp of when the event was recorded.",
                "submission": "Object describing the submission at that moment.",
            },
        },
        "verification": {
            "importance": (
                "Your webhook URL is publicly reachable. Anyone can POST to "
                "it. The signature is the only thing that proves an event "
                "came from us - verify before you act on it."
            ),
            "secret": (
                "Your signing secret, from GET /mie/v1/me/. It is reissued "
                "if your account is rejected and re-approved."
            ),
            "algorithm": "HMAC-SHA256",
            "signed_string": "'{X-MIE-Timestamp}.{raw request body bytes}'",
            "steps": [
                "Read the raw request body as bytes BEFORE any JSON parsing. Re-serializing changes the bytes and the signature will not match.",
                "Read X-MIE-Timestamp.",
                "Compute HMAC-SHA256 over f'{timestamp}.' + raw_body using your signing secret; hex-encode it lowercase.",
                "Compare against X-MIE-Signature using a constant-time comparison.",
                f"Reject the event if the timestamp is more than {webhook_dispatcher.REPLAY_WINDOW_SECONDS} seconds old - this is your replay defence and we do not enforce it for you.",
            ],
            "replay_window_seconds": webhook_dispatcher.REPLAY_WINDOW_SECONDS,
            "examples": {
                "python": (
                    "import hmac, hashlib, time\n\n"
                    "def verify(raw_body: bytes, timestamp: str, signature: str, secret: str) -> bool:\n"
                    f"    if abs(time.time() - int(timestamp)) > {webhook_dispatcher.REPLAY_WINDOW_SECONDS}:\n"
                    "        return False\n"
                    "    expected = hmac.new(\n"
                    "        secret.encode(), timestamp.encode() + b'.' + raw_body, hashlib.sha256\n"
                    "    ).hexdigest()\n"
                    "    return hmac.compare_digest(expected, signature)"
                ),
                "node": (
                    "const crypto = require('crypto');\n\n"
                    "function verify(rawBody, timestamp, signature, secret) {\n"
                    f"  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > {webhook_dispatcher.REPLAY_WINDOW_SECONDS}) return false;\n"
                    "  const expected = crypto\n"
                    "    .createHmac('sha256', secret)\n"
                    "    .update(Buffer.concat([Buffer.from(timestamp + '.'), rawBody]))\n"
                    "    .digest('hex');\n"
                    "  const a = Buffer.from(expected);\n"
                    "  const b = Buffer.from(signature);\n"
                    "  return a.length === b.length && crypto.timingSafeEqual(a, b);\n"
                    "}"
                ),
                "note": (
                    "In Express, use express.raw({type: 'application/json'}) "
                    "on this route. express.json() discards the raw bytes "
                    "and makes verification impossible."
                ),
            },
        },
        "retries": {
            "max_attempts": webhook_dispatcher.MAX_ATTEMPTS,
            "backoff_seconds": list(webhook_dispatcher.RETRY_DELAYS_SECONDS),
            "schedule": [
                {
                    "after_failed_attempt": index + 1,
                    "retries_in_seconds": delay,
                    "human": _humanise_seconds(delay),
                }
                for index, delay in enumerate(webhook_dispatcher.RETRY_DELAYS_SECONDS)
            ],
            "total_window": (
                f"{webhook_dispatcher.MAX_ATTEMPTS} attempts spread over "
                f"roughly {_humanise_seconds(sum(webhook_dispatcher.RETRY_DELAYS_SECONDS))} "
                "from the first try."
            ),
            "exhausted": (
                f"After {webhook_dispatcher.MAX_ATTEMPTS} failed attempts "
                f"the event is marked {WebhookDeliveryStatus.FAILED.value} "
                "and never retried. There is no self-service replay - "
                "reconcile from your queue endpoint instead."
            ),
            "delivery_statuses": [
                {
                    "status": member.value,
                    "meaning": {
                        WebhookDeliveryStatus.PENDING: "Recorded, not yet delivered, still eligible for attempts.",
                        WebhookDeliveryStatus.DELIVERED: "You returned 2xx.",
                        WebhookDeliveryStatus.FAILED: "Attempts exhausted, or your account was rejected.",
                    }[member],
                }
                for member in WebhookDeliveryStatus
            ],
        },
        "idempotency": {
            "key": "event_id",
            "rule": (
                "Retries reuse the same event_id. Store processed event_ids "
                "and make repeat deliveries a no-op - assume at-least-once "
                "delivery, never exactly-once."
            ),
            "reversals": (
                "Reversals are NOT retries. Re-approving a rejected idea "
                "produces a brand new event_id with type "
                "SUBMISSION_APPROVED. Deduplicate on event_id, but let the "
                "newest event win per submission."
            ),
        },
        "account_state_effects": [
            {
                "account_status": DeveloperAccountStatus.APPROVED.value,
                "effect": "Events are delivered normally.",
            },
            {
                "account_status": DeveloperAccountStatus.SUSPENDED.value,
                "effect": (
                    "Events keep being recorded but no delivery is "
                    "attempted. They stay PENDING and are delivered if the "
                    "account returns to APPROVED - nothing is lost."
                ),
            },
            {
                "account_status": DeveloperAccountStatus.REJECTED.value,
                "effect": (
                    "All pending events are immediately marked FAILED and "
                    "dropped. Rejection is terminal and undelivered events "
                    "do not survive it."
                ),
            },
            {
                "account_status": DeveloperAccountStatus.PENDING.value,
                "effect": "No submissions exist yet, so no events exist.",
            },
        ],
        "receiver_requirements": [
            "HTTPS with a certificate that validates. We do not deliver to endpoints we cannot verify.",
            "Publicly reachable - no VPN, no IP allowlist that excludes us, no basic auth.",
            f"Responds within {webhook_dispatcher.READ_TIMEOUT_SECONDS} seconds. Queue the work; do not process inline.",
            "Idempotent on event_id.",
            "Accepts POST with a JSON body and returns 2xx on success.",
        ],
        "events": _webhook_event_catalogue(),
    }


def _webhook_event_catalogue() -> list[dict]:
    """One entry per live WebhookEventType, with a real wire-shape sample."""

    catalogue = []
    for event_type in WebhookEventType:
        docs = WEBHOOK_EVENT_DOCS[event_type]
        status = docs["resulting_status"]
        display_status = status or SubmissionStatus.APPROVED

        submission = {
            "reference": _sample_reference(display_status),
            "status": display_status.value,
            "title": SAMPLE_TITLE,
        }
        if "rejection_reason" in docs["extra_fields"]:
            submission["rejection_reason"] = "Already covered by the live catalogue"
            submission["rejection_note"] = "The existing Rust course covers this ground."
        if "payout_bypass" in docs["extra_fields"]:
            submission["payout_bypass"] = True

        catalogue.append(
            {
                "type": event_type.value,
                "label": event_type.label,
                "fires_when": docs["fires_when"],
                "resulting_status": status.value if status else None,
                "status_unchanged": status is None,
                "extra_submission_fields": docs["extra_fields"],
                "sample_body": {
                    "event_id": "8f14e45f-ceea-4e78-9a1b-2c3d4e5f6a7b",
                    "type": event_type.value,
                    "occurred_at": "2026-08-24T15:30:00.123456+00:00",
                    "submission": submission,
                },
            }
        )
    return catalogue


def _errors() -> dict:
    return {
        "envelope": {
            "shape": {
                "errors": [
                    {
                        "type": "validation_error | client_error | server_error",
                        "code": "machine-readable code - branch on this, not on the message",
                        "message": "human-readable description",
                        "field_name": "the offending field, or null",
                    }
                ]
            },
            "note": (
                "Every non-2xx response uses this envelope. `errors` is "
                "always a list, even for a single problem, and `message` "
                "is for humans - it is not a stable contract."
            ),
        },
        "statuses": [
            {
                "status": 400,
                "type": "validation_error",
                "when": "The request body is malformed or fails field validation.",
                "retry": "No. Fix the payload.",
                "example": {
                    "errors": [
                        {
                            "type": "validation_error",
                            "code": "required",
                            "message": "A non-empty string title is required.",
                            "field_name": "title",
                        }
                    ]
                },
            },
            {
                "status": 401,
                "type": "client_error",
                "when": "Credentials are missing, invalid, suspended, or inactive. See authentication.failure_codes.",
                "retry": "No, not without fixing credentials.",
                "example": {
                    "errors": [
                        {
                            "type": "client_error",
                            "code": "invalid_api_key",
                            "message": "Invalid API key.",
                            "field_name": None,
                        }
                    ]
                },
            },
            {
                "status": 403,
                "type": "client_error",
                "when": "Authenticated but not permitted. On the developer surface this means the account is not APPROVED.",
                "retry": "No.",
                "example": {
                    "errors": [
                        {
                            "type": "client_error",
                            "code": "permission_denied",
                            "message": "Active approved developer credentials are required.",
                            "field_name": None,
                        }
                    ]
                },
            },
            {
                "status": 404,
                "type": "client_error",
                "when": "The resource does not exist, or is not yours.",
                "retry": "No.",
                "example": {
                    "errors": [
                        {
                            "type": "client_error",
                            "code": "not_found",
                            "message": "Not found.",
                            "field_name": None,
                        }
                    ]
                },
            },
            {
                "status": 429,
                "type": "client_error",
                "when": "A rate limit was exceeded. Read the Retry-After header.",
                "retry": "Yes, after Retry-After seconds.",
                "example": {
                    "errors": [
                        {
                            "type": "client_error",
                            "code": "throttled",
                            "message": "Request was throttled. Expected available in 42 seconds.",
                            "field_name": None,
                        }
                    ]
                },
            },
            {
                "status": 500,
                "type": "server_error",
                "when": "Something broke on our side.",
                "retry": "Yes, with exponential backoff. If it persists, contact support with the timestamp.",
                "example": {
                    "errors": [
                        {
                            "type": "server_error",
                            "code": "error",
                            "message": "A server error occurred.",
                            "field_name": None,
                        }
                    ]
                },
            },
        ],
        "retry_guidance": (
            "Retry 429 (after Retry-After) and 5xx with exponential "
            "backoff and jitter. Never blind-retry a 4xx - and remember "
            "submission POSTs are not idempotent, so a retried timeout can "
            "create a second submission."
        ),
    }


def _rate_limits() -> dict:
    return {
        "scope": (
            "Limits are per client for the endpoints that declare them. "
            "Endpoints not listed here are not rate limited."
        ),
        "on_exceed": (
            "HTTP 429 with a Retry-After header carrying the seconds to "
            "wait. Respect it rather than retrying immediately."
        ),
        "limits": [
            {
                "endpoint": f"POST {API_ROOT}/mie/v1/register/",
                "limit": _rate("mie_register"),
                "why": "Public and pre-auth; it creates database rows for anonymous callers.",
            },
            {
                "endpoint": f"POST {API_ROOT}/mie/v1/submissions/",
                "limit": _rate("mie_ingest"),
                "why": "Ingestion runs three database checks per call.",
                "advice": (
                    "For bulk imports, pace yourself under this ceiling "
                    "rather than bursting into 429s. There is no batch "
                    "endpoint - one idea per request."
                ),
            },
        ],
    }


def _pagination() -> dict:
    return {
        "applies_to": [f"GET {API_ROOT}/mie/v1/submissions/queue/"],
        "style": "Page number",
        "query_parameters": {
            "page": "1-based page number. Defaults to 1.",
            "size": f"Rows per page. Defaults to {settings.REST_FRAMEWORK['PAGE_SIZE']}.",
        },
        "envelope": {
            "status": "boolean, always true on success",
            "message": "string",
            "data.paginator.count": "total matching rows",
            "data.paginator.page": "current page number",
            "data.paginator.page_size": "rows per page",
            "data.paginator.total_pages": "total pages",
            "data.paginator.next": "absolute URL of the next page, or null",
            "data.paginator.next_page_number": "integer or null",
            "data.paginator.previous": "absolute URL of the previous page, or null",
            "data.paginator.previous_page_number": "integer or null",
            "data.results": "the array of rows",
        },
        "note": (
            "Requesting a page beyond the last returns 404, except when a "
            "`search` filter is active - an empty search result returns an "
            "empty page 1 rather than an error."
        ),
    }


def _go_live_checklist() -> list[dict]:
    return [
        {
            "item": "API key is in a secret store, not in source control",
            "why": "It cannot be rotated self-service, and it is a bearer credential.",
        },
        {
            "item": "Signing secret fetched from /me and stored alongside it",
            "why": "Without it you cannot verify a single webhook.",
        },
        {
            "item": "Webhook endpoint verifies the HMAC before acting",
            "why": "Your URL is public; anyone can POST to it.",
        },
        {
            "item": "Webhook endpoint reads the RAW body for verification",
            "why": "Parsing and re-serializing changes the bytes and every signature check will fail.",
        },
        {
            "item": f"Webhook endpoint rejects timestamps older than {webhook_dispatcher.REPLAY_WINDOW_SECONDS}s",
            "why": "We do not enforce the replay window for you.",
        },
        {
            "item": "Webhook endpoint returns 2xx in well under "
            f"{webhook_dispatcher.READ_TIMEOUT_SECONDS}s and queues the work",
            "why": "A slow 200 counts as a failure and burns a retry attempt.",
        },
        {
            "item": "Processed event_ids are recorded and repeats are no-ops",
            "why": "Delivery is at-least-once.",
        },
        {
            "item": "Newest event wins per submission",
            "why": "Decisions are reversible and events can arrive out of order.",
        },
        {
            "item": "Submission POSTs branch on `status`, not on HTTP 201",
            "why": "Three of the four ingestion outcomes are dead ends.",
        },
        {
            "item": "Records are keyed on submission `id`, not on `reference`",
            "why": "The reference suffix mutates with status.",
        },
        {
            "item": "A reconciliation job reads the queue endpoint periodically",
            "why": "Exhausted webhook deliveries are never replayed; the queue is the fallback.",
        },
        {
            "item": "429 handling respects Retry-After",
            "why": "Hammering through a throttle just extends it.",
        },
    ]


def _faq() -> list[dict]:
    return [
        {
            "question": "I lost my API key. Can you resend it?",
            "answer": (
                "No. Only a SHA-256 hash is stored; the plaintext key "
                f"exists nowhere on our side. Email {SUPPORT_EMAIL} to have "
                "a superadmin re-issue credentials. Note that re-issuance "
                "goes through rejection, which drops undelivered webhook "
                "events, so pick a quiet window."
            ),
        },
        {
            "question": "My submission came back with a 201 but nothing is in review. Why?",
            "answer": (
                "It was deduplicated. Read the `status` field: "
                "DUPLICATE_IN_QUEUE, DUPLICATE_EXISTING and "
                "PREVIOUSLY_REJECTED are all dead ends that never reach a "
                "reviewer. Only PENDING_REVIEW continues."
            ),
        },
        {
            "question": "How do I get a rejected idea reconsidered?",
            "answer": (
                "Resubmitting the same title will short-circuit to "
                "PREVIOUSLY_REJECTED forever. Change the title "
                "meaningfully, or contact support to have the original "
                "decision reversed - admins can flip a rejection back to "
                "approved, which re-fires SUBMISSION_APPROVED."
            ),
        },
        {
            "question": "Every signature check fails. What am I doing wrong?",
            "answer": (
                "Almost always the raw body. Frameworks that parse JSON "
                "before your handler runs give you a re-serialized body "
                "with different bytes. Capture the raw bytes - "
                "express.raw() in Express, request.body in Django, "
                "await request.body() in FastAPI - and sign "
                "f'{timestamp}.' + raw_bytes."
            ),
        },
        {
            "question": "Can I get an event replayed?",
            "answer": (
                "No. Once attempts are exhausted the event is terminal. "
                "Reconcile from GET /mie/v1/submissions/queue/, which "
                "always shows current state for every submission you own."
            ),
        },
        {
            "question": "Can I submit several ideas in one request?",
            "answer": (
                "No. One idea per POST. Pace bulk imports under the "
                f"{_rate('mie_ingest')} ingest limit."
            ),
        },
        {
            "question": "Why did I get SUBMISSION_APPROVED for something already approved?",
            "answer": (
                "Either a retry of the same event - check event_id - or a "
                "genuine re-approval after a reversal, which carries a new "
                "event_id. Dedupe on event_id and let the newest event win."
            ),
        },
        {
            "question": "Can I change my webhook URL myself?",
            "answer": (
                "Not today. The URL is fixed at registration; email "
                f"{SUPPORT_EMAIL} to have it changed."
            ),
        },
        {
            "question": "Does my API key expire?",
            "answer": (
                "No. It stays valid until the account is rejected or "
                "credentials are re-issued. Suspension freezes it without "
                "destroying it."
            ),
        },
        {
            "question": "Someone else submitted my title first. What now?",
            "answer": (
                "Dedup is platform-wide, not per developer, so their queued "
                "title blocks yours with DUPLICATE_IN_QUEUE. Titles are "
                "first-come, first-served; submit promptly and pick "
                "distinctive titles."
            ),
        },
    ]


# ── Small helpers ────────────────────────────────────────────────────


def _rate(scope: str) -> str:
    """The live throttle rate for `scope`, in human form."""

    raw = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"][scope]
    count, _, period = raw.partition("/")
    periods = {"s": "second", "min": "minute", "hour": "hour", "day": "day"}
    return f"{count} requests per {periods.get(period, period)}"


def _humanise_seconds(seconds: int) -> str:
    """'60' -> '1 minute', '3600' -> '1 hour', '5400' -> '1.5 hours'."""

    if seconds < 60:
        return _plural(seconds, "second")
    if seconds < 3600:
        return _plural(seconds / 60, "minute")
    return _plural(seconds / 3600, "hour")


def _plural(amount: float, unit: str) -> str:
    rendered = f"{amount:g}"
    return f"{rendered} {unit}" if rendered == "1" else f"{rendered} {unit}s"


def _iso(value):
    return value.isoformat() if value else None
