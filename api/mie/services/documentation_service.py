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
    course_upload        pushing the course for an approved idea, end to end
    course_schema        every field of the push body
    media                where course media can live; uploading to us
    course_lifecycle     every status a pushed course passes through
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
    WEBHOOK_ALL_EVENTS,
    SubmissionStatus,
    WebhookDeliveryStatus,
    WebhookEventType,
)
from api.courses.constants import COURSE_MEDIA_URL_MAX_LENGTH
from api.courses.enums import (
    CourseStatus,
    DifficultyLevel,
    LessonContentType,
    QuestionType,
)
from api.courses.serializers.assessment_serializer import (
    MAXIMUM_CHOICE_OPTIONS,
    MINIMUM_CHOICE_OPTIONS,
)
from api.mie.models.course_submission import (
    CONFIDENCE_NOTE_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
)
from api.mie.serializers.course_push_serializer import (
    MAX_PUSH_BLOCKS_PER_LESSON,
    MAX_PUSH_LESSONS_PER_MODULE,
    MAX_PUSH_MODULES,
)
from api.mie.services import webhook_dispatcher
from api.mie.services.course_push_service import MIE_UPLOAD_PURPOSES
from api.mie.services.key_service import API_KEY_PREFIX
from api.mie.services.reference import REFERENCE_SUFFIXES
from api.mie.services.submission_service import EVENT_TYPE_BY_STATUS
from api.mie.services.webhook_endpoint_service import (
    MAX_WEBHOOK_ENDPOINTS,
    events_for,
    live_endpoints,
)
from api.reviews.enums import ReviewStage
from shared.constants.authentication import SUPPORT_EMAIL
from shared.services.storage_service import (
    COURSE_UPLOAD_RULES,
    MIN_MEDIA_HEIGHT,
    MIN_MEDIA_WIDTH,
)

DOCUMENTATION_VERSION = "3.1.0"
"""Bump when the shape of this document changes, not when values change."""

API_ROOT = "/api/v1"

SAMPLE_SUBMISSION_ID = "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11"
SAMPLE_SHORT_ID = SAMPLE_SUBMISSION_ID.replace("-", "")[:8]
SAMPLE_TITLE = "Build a Production-Grade Rust Course"
SAMPLE_COURSE_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SAMPLE_CATEGORY_ID = "3f2b9c1e-5d4a-4e8f-9b7c-2a1d0e6f4c3b"
SAMPLE_VERSION_ID = "b6e2d1a4-9c8f-4a3e-8d2b-1f0e9c8b7a6d"
SAMPLE_REVISION_FEEDBACK = {
    "stage": ReviewStage.CONTENT.value,
    "rejected_at": "2026-09-02T11:20:00+00:00",
    "feedback": {
        "summary": "Module 2 needs worked examples and a longer preview video.",
    },
    "flags": [
        {
            "flag_type": "script_length",
            "title": "Script too short",
            "system_message": "306/500 words below minimum",
            "reviewer_note": "Expand the walkthrough with a second example.",
            "module_title": "Async services",
            "lesson_title": "Tokio in one hour",
        }
    ],
}

SAMPLE_COURSE_PUSH = {
    "title": SAMPLE_TITLE,
    "description": (
        "<what the course teaches, who it is for and what they need first - "
        "within the description word range from /course-requirements/>"
    ),
    "category": SAMPLE_CATEGORY_ID,
    "version": SAMPLE_VERSION_ID,
    "difficulty_level": DifficultyLevel.ADVANCED.value,
    "preview_video_url": "https://videos.studio.io/rust/preview.mp4",
    "thumbnail_url": "https://videos.studio.io/rust/cover.jpg",
    "learning_objectives": [
        "Design ownership-safe APIs",
        "Profile and optimise async services",
        "Ship a production Rust service",
    ],
    "tags": ["rust", "backend"],
    "duration_hours": 6,
    "terms_accepted": True,
    "modules": [
        {
            "title": "Ownership in practice",
            "description": "Borrowing, lifetimes and the patterns that avoid fighting them.",
            "learning_objectives": ["Explain ownership", "Refactor borrow errors"],
            "lessons": [
                {
                    "title": "Why ownership exists",
                    "lesson_type": LessonContentType.TEXT.value,
                    "script": "<the lesson text, within the script word range>",
                    "learning_objectives": ["Describe a move", "Describe a borrow"],
                    "duration_minutes": 12,
                    "lesson_requirement": "Comfortable reading Rust function signatures.",
                },
                {
                    "title": "Borrowing walkthrough",
                    "lesson_type": LessonContentType.VIDEO.value,
                    "video_url": "https://www.youtube.com/watch?v=abc123",
                    "learning_objectives": ["Borrow immutably", "Borrow mutably"],
                    "duration_minutes": 15,
                    "content_blocks": [
                        {"block_type": "HEADING_1", "text_content": "Borrowing"},
                        {"block_type": "PARAGRAPH", "text_content": "Watch, then try the exercise."},
                        {"block_type": "IMAGE", "media_url": "https://videos.studio.io/rust/borrow.png"},
                    ],
                    "assessment": {
                        "title": "Quick check",
                        "questions": [
                            {
                                "type": QuestionType.SINGLE_CHOICE.value,
                                "question": "Can two mutable borrows of one value coexist?",
                                "options": ["Yes", "No"],
                                "correct_index": 1,
                                "points": 1,
                            }
                        ],
                    },
                },
            ],
            "assessment": {
                "title": "Ownership check",
                "questions": [
                    {
                        "type": QuestionType.MULTIPLE_CHOICE.value,
                        "question": "Which of these move a String?",
                        "options": ["take(s)", "len(&s)", "let t = s;"],
                        "correct_indices": [0, 2],
                        "points": 2,
                    },
                    {
                        "type": QuestionType.ESSAY.value,
                        "question": "When would you reach for Rc<RefCell<T>>?",
                        "expected_answer": "Shared ownership with interior mutability in single-threaded code.",
                        "points": 3,
                    },
                ],
            },
        },
        "<... more modules, within the module range from /course-requirements/>",
    ],
    "final_assessment": {
        "title": "Final exam",
        "questions": ["<at least the final-assessment minimum from /course-requirements/>"],
    },
}
"""A full push body. Placeholders in <angle brackets> stand for content."""


def _sample_reference(status: SubmissionStatus) -> str:
    """A realistic reference for `status`, built the way the model builds it."""

    return f"SCB-{SAMPLE_SHORT_ID}-{REFERENCE_SUFFIXES[status]}"


# ── Prose keyed by live enum members ─────────────────────────────────
# Every dict below is keyed by an enum member, so adding a member without
# describing it raises a KeyError in the tests rather than silently
# shipping a documentation gap.

PLAN_EXPLANATIONS = {
    MiePlanType.PAID_PER_SUBMISSION: (
        "You are paid for each course that is produced from one of your "
        "approved ideas and published. Approving an idea pays nothing on "
        "its own. Nothing you send is exempt unless your plan is changed."
    ),
    MiePlanType.BYPASS_PER_SUBMISSION: (
        "A course published from one of your approved ideas pays out by "
        "default, but a superadmin can mark an individual submission "
        "payout_bypass=true, which excludes just that idea. You are told the moment it happens via the "
        "SUBMISSION_PAYOUT_BYPASS_UPDATED webhook."
    ),
    MiePlanType.BYPASS_ACCOUNT: (
        "Nothing from this account pays out, by prior agreement. "
        "Approvals still happen and courses are still produced and "
        "published - they simply carry no payment, and payout_bypass on "
        "individual submissions is irrelevant to you."
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
            "Write the course and push it to POST "
            "/mie/v1/submissions/<id>/course/ - see course_upload. Note the "
            "approval is reversible - a later SUBMISSION_REJECTED for the "
            "same reference supersedes it, and blocks further pushes."
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
        "fires_when": "Ingestion found a course on the platform, in any status, with this exact title.",
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
    WebhookEventType.COURSE_SUBMITTED: {
        "fires_when": (
            "A course you pushed for this idea was accepted and submitted for "
            "review - on the first push and on every accepted revision."
        ),
        "resulting_status": None,
        "extra_fields": ["course"],
    },
    WebhookEventType.COURSE_TEXT_APPROVED: {
        "fires_when": (
            "The text of your course passed the first review seat. The "
            "course is now AWAITING_VIDEO: send the video with POST "
            "/mie/v1/submissions/{id}/course/video/. Only fires when the "
            "platform reviews text before video."
        ),
        "resulting_status": None,
        "extra_fields": ["course"],
    },
    WebhookEventType.COURSE_REVISION_REQUESTED: {
        "fires_when": (
            "A reviewer - in content review or QA verification - sent your "
            "course back, to DRAFT or, when the platform reviews text before "
            "video, to NEEDS_REVISION. course.revision_feedback says what to "
            "fix; push the corrected course to the same idea."
        ),
        "resulting_status": None,
        "extra_fields": ["course"],
        "extra_course_fields": ["revision_feedback"],
    },
    WebhookEventType.COURSE_PUBLISHED: {
        "fires_when": (
            "Your course passed content review and QA verification and was "
            "published. Publication is one-way."
        ),
        "resulting_status": None,
        "extra_fields": ["course"],
    },
}

COURSE_STATUS_BY_EVENT = {
    WebhookEventType.COURSE_SUBMITTED: CourseStatus.SUBMITTED,
    WebhookEventType.COURSE_TEXT_APPROVED: CourseStatus.AWAITING_VIDEO,
    WebhookEventType.COURSE_REVISION_REQUESTED: CourseStatus.DRAFT,
    WebhookEventType.COURSE_PUBLISHED: CourseStatus.PUBLISHED,
}
"""The course status each COURSE_* event announces, for the samples."""

COURSE_STATUS_DOCS = {
    CourseStatus.DRAFT: {
        "meaning": (
            "A reviewer sent the course back. Nothing is in review until you "
            "push again."
        ),
        "your_move": (
            "Read revision_feedback (webhook or GET .../course/), fix the "
            "course, and push it again to the same idea."
        ),
    },
    CourseStatus.SUBMITTED: {
        "meaning": "Accepted and waiting for a content reviewer to pick it up.",
        "your_move": "Nothing. Pushing again now returns 409.",
    },
    CourseStatus.IN_REVIEW: {
        "meaning": "A content reviewer is working through it.",
        "your_move": "Nothing. Pushing again now returns 409.",
    },
    CourseStatus.NEEDS_REVISION: {
        "meaning": (
            "A reviewer sent the course back while the platform reviews text "
            "before video. Nothing is in review until you push again; the "
            "course resumes at the seat that sent it back."
        ),
        "your_move": "Read the feedback and push again, as for DRAFT.",
    },
    CourseStatus.AWAITING_VIDEO: {
        "meaning": (
            "The text passed the first review seat and the platform is "
            "waiting for the video."
        ),
        "your_move": (
            "Send the video with POST /mie/v1/submissions/{id}/course/video/."
        ),
    },
    CourseStatus.QA_VERIFICATION: {
        "meaning": (
            "Content review passed; a QA reviewer is checking media, "
            "accessibility and quizzes."
        ),
        "your_move": "Nothing. Pushing again now returns 409.",
    },
    CourseStatus.APPROVED: {
        "meaning": "Passed every review seat and waiting to be published.",
        "your_move": "Nothing. Pushing again now returns 409.",
    },
    CourseStatus.PUBLISHED: {
        "meaning": "Live. Publication is one-way and cannot be replaced.",
        "your_move": "Nothing. COURSE_PUBLISHED has fired.",
    },
    CourseStatus.ARCHIVED: {
        "meaning": "Taken out of circulation by the platform.",
        "your_move": f"Contact {SUPPORT_EMAIL} if you did not expect it.",
    },
    CourseStatus.REJECTED: {
        "meaning": (
            "Never stored on a course - a rejection returns the course to "
            "DRAFT. Listed for completeness."
        ),
        "your_move": "Nothing; you will see DRAFT instead.",
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
        "course_upload": _course_upload(account, base_url),
        "course_schema": _course_schema(),
        "media": _media(base_url),
        "course_lifecycle": _course_lifecycle(),
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
            "External developers integrating with the MIE (Market "
            "Intelligence Engine): submitting course ideas, and pushing the "
            "finished course for each approved one."
        ),
        "read_this_if": (
            "You want to submit course ideas programmatically, push the "
            "courses you write for the approved ones, and follow both "
            "through review without polling us or emailing support."
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
        "webhook_endpoints": _endpoint_summaries(account),
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
        "how_to_manage_webhooks": (
            "Self-service. Add, change or delete endpoints, and choose the "
            f"events each one receives, at {base_url}{API_ROOT}/mie/v1/webhooks/ "
            "with your API key, or from your developer profile. See "
            "`webhooks.endpoints_and_subscriptions`."
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
                    "A signed POST lands on each of your webhook endpoints "
                    "that takes the event, within a minute. Recompute the HMAC before trusting it - see "
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
            {
                "step": 6,
                "title": "Push the course once the idea is approved",
                "detail": (
                    "When SUBMISSION_APPROVED arrives, write the course and "
                    "push it to the idea. The course_upload section walks "
                    "through it end to end."
                ),
                "curl": (
                    f"curl -sS -X POST {base_url}{API_ROOT}/mie/v1/submissions/"
                    f"{SAMPLE_SUBMISSION_ID}/course/ \\\n"
                    f'  -H "{API_KEY_HEADER}: {key_example}" \\\n'
                    '  -H "Content-Type: application/json" \\\n'
                    "  --data @course.json"
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
            "Pushing a course before the idea is APPROVED. It returns 409 "
            "and stores nothing.",
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
            "name": "Course upload",
            "actor": "You",
            "what_happens": (
                "You write the course for the approved idea and push it in "
                "one request, in the platform's course schema. It is built "
                "and checked against the same structural rules a creator's "
                "course faces at submit; if anything fails, nothing is "
                "stored and every failure is returned."
            ),
            "your_move": (
                "Read /mie/v1/course-requirements/, host or upload your "
                "media, then POST /mie/v1/submissions/<id>/course/. See "
                "course_upload."
            ),
            "you_can_authenticate": True,
            "webhook_fired": WebhookEventType.COURSE_SUBMITTED.value,
        },
        {
            "stage": 8,
            "name": "Course review",
            "actor": "Platform reviewers",
            "what_happens": (
                "Your course goes through the platform's content review "
                "seats and then QA verification, like every course. A "
                "rejection at any seat returns it to DRAFT with the "
                "reviewer's feedback."
            ),
            "your_move": (
                f"On {WebhookEventType.COURSE_REVISION_REQUESTED.value}, fix "
                "the course and push the complete course again to the same "
                "idea. Otherwise, wait."
            ),
            "you_can_authenticate": True,
            "webhook_fired": (
                f"{WebhookEventType.COURSE_REVISION_REQUESTED.value} "
                "(when sent back)"
            ),
        },
        {
            "stage": 9,
            "name": "Publication",
            "actor": "Platform",
            "what_happens": (
                "The approved course is published. Publication is one-way: "
                "rejecting the idea later does not unpublish its course, "
                "and the link between idea and course is kept."
            ),
            "your_move": "Record the publication.",
            "you_can_authenticate": True,
            "webhook_fired": WebhookEventType.COURSE_PUBLISHED.value,
        },
        {
            "stage": 10,
            "name": "Payout",
            "actor": "Platform",
            "what_happens": (
                "You are paid when a course produced from your approved "
                "idea is published - not when the idea is approved. Whether "
                "that publication carries payment is governed by your "
                "account plan_type and, where the plan allows it, the "
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
            "not exposed on the developer API. Publication of the course "
            "produced from your idea is the trigger, not approval; "
            "the credit itself is not something you can query here."
        ),
        "course_price": (
            "The amount a course pays is fixed when it is submitted: the "
            "category's price for the course's difficulty_level at that "
            "moment. A later price change does not move it."
        ),
        "settlement_status": (
            "Automatic settlement for courses pushed over this API is not "
            "live yet. Published courses are recorded against your "
            f"account with their price; contact {SUPPORT_EMAIL} about "
            "settlement until it is."
        ),
    }


SAMPLE_ALL_EVENTS_ENDPOINT = {
    "id": "5b1f0c9e-2d4a-4f7e-9a3b-8c6d1e2f3a4b",
    "url": "https://hooks.studio.io/mie",
    "events": [WEBHOOK_ALL_EVENTS],
    "created_datetime": "2026-08-23T08:55:00Z",
    "updated_datetime": "2026-08-23T08:55:00Z",
}

SAMPLE_COURSE_EVENTS_ENDPOINT = {
    "id": "9a7c3e51-0b2d-4c8f-a6e4-1d3f5b7c9e02",
    "url": "https://hooks.studio.io/mie/courses",
    "events": [
        WebhookEventType.COURSE_TEXT_APPROVED.value,
        WebhookEventType.COURSE_REVISION_REQUESTED.value,
        WebhookEventType.COURSE_PUBLISHED.value,
    ],
    "created_datetime": "2026-10-08T09:00:00Z",
    "updated_datetime": "2026-10-08T09:00:00Z",
}


def _endpoint_summaries(account) -> list[dict]:
    """The caller's live endpoints, as /mie/v1/webhooks/ returns them.

    Reads the `live_webhook_endpoints` prefetch when the caller supplies it,
    as DeveloperAccountAdminSerializer does, and queries otherwise.
    """

    endpoints = getattr(account, "live_webhook_endpoints", None)
    if endpoints is None:
        endpoints = live_endpoints(developer=account)
    return [
        {"id": str(endpoint.id), "url": endpoint.url, "events": events_for(endpoint)}
        for endpoint in endpoints
    ]


def _webhook_endpoint_routes(prefix: str) -> list[dict]:
    """The routes that manage webhook endpoints."""

    auth = f"{API_KEY_HEADER} (or Bearer session token)"
    events_field = (
        f'array of strings: ["{WEBHOOK_ALL_EVENTS}"] for every event type, '
        f"or one or more of {WebhookEventType.values}"
    )
    not_found = {"status": 404, "when": "The endpoint is not yours, or was deleted."}
    unauthorised = {"status": 401, "when": "Missing, invalid, suspended, or inactive credentials."}
    return [
        {
            "name": "List your webhook endpoints",
            "method": "GET",
            "path": f"{API_ROOT}/mie/v1/webhooks/",
            "url": f"{prefix}/mie/v1/webhooks/",
            "auth": auth,
            "rate_limit": "None",
            "purpose": "Every URL you receive webhooks on, and the events each takes.",
            "success_status": 200,
            "response_example": [SAMPLE_ALL_EVENTS_ENDPOINT, SAMPLE_COURSE_EVENTS_ENDPOINT],
            "errors": [unauthorised],
            "notes": [
                f"Not paginated: an account keeps at most {MAX_WEBHOOK_ENDPOINTS} endpoints.",
            ],
        },
        {
            "name": "Add a webhook endpoint",
            "method": "POST",
            "path": f"{API_ROOT}/mie/v1/webhooks/",
            "url": f"{prefix}/mie/v1/webhooks/",
            "auth": auth,
            "rate_limit": "None",
            "purpose": "Receive events at another URL: every event, or the ones you choose.",
            "request_body": {
                "url": "string, required, HTTPS URL",
                "events": f"{events_field}; required",
            },
            "request_example": {
                "url": SAMPLE_COURSE_EVENTS_ENDPOINT["url"],
                "events": SAMPLE_COURSE_EVENTS_ENDPOINT["events"],
            },
            "success_status": 201,
            "response_example": SAMPLE_COURSE_EVENTS_ENDPOINT,
            "errors": [
                {"status": 400, "when": "A field is missing, the URL is malformed, or an event type is unknown or mixed with \"all\"."},
                unauthorised,
                {"status": 409, "when": "webhook_endpoint_duplicate: you already have an endpoint for this URL. webhook_endpoint_limit: you already have the maximum."},
            ],
            "notes": ["Applies to events recorded from now on."],
        },
        {
            "name": "Show one webhook endpoint",
            "method": "GET",
            "path": f"{API_ROOT}/mie/v1/webhooks/<endpoint_id>/",
            "url": f"{prefix}/mie/v1/webhooks/{SAMPLE_COURSE_EVENTS_ENDPOINT['id']}/",
            "auth": auth,
            "rate_limit": "None",
            "purpose": "One endpoint and the events it takes.",
            "success_status": 200,
            "response_example": SAMPLE_COURSE_EVENTS_ENDPOINT,
            "errors": [unauthorised, not_found],
        },
        {
            "name": "Change a webhook endpoint",
            "method": "PATCH",
            "path": f"{API_ROOT}/mie/v1/webhooks/<endpoint_id>/",
            "url": f"{prefix}/mie/v1/webhooks/{SAMPLE_COURSE_EVENTS_ENDPOINT['id']}/",
            "auth": auth,
            "rate_limit": "None",
            "purpose": "Change the URL, the events, or both. Send only what changes, as often as you like.",
            "request_body": {
                "url": "string, optional, HTTPS URL",
                "events": f"{events_field}; optional, replaces the whole list",
            },
            "request_example": {"events": [WEBHOOK_ALL_EVENTS]},
            "success_status": 200,
            "response_example": {**SAMPLE_COURSE_EVENTS_ENDPOINT, "events": [WEBHOOK_ALL_EVENTS]},
            "errors": [
                {"status": 400, "when": "Empty body, malformed URL, or an unknown event type."},
                unauthorised,
                not_found,
                {"status": 409, "when": "webhook_endpoint_duplicate: the new URL is already another of your endpoints."},
            ],
            "notes": ["Deliveries already queued still go to this endpoint, at its current URL."],
        },
        {
            "name": "Delete a webhook endpoint",
            "method": "DELETE",
            "path": f"{API_ROOT}/mie/v1/webhooks/<endpoint_id>/",
            "url": f"{prefix}/mie/v1/webhooks/{SAMPLE_COURSE_EVENTS_ENDPOINT['id']}/",
            "auth": auth,
            "rate_limit": "None",
            "purpose": "Stop sending events to an endpoint.",
            "success_status": 204,
            "errors": [
                unauthorised,
                not_found,
                {"status": 409, "when": "last_webhook_endpoint: it is your only endpoint. Change it instead."},
            ],
            "notes": ["Deliveries still queued for it are dropped, not moved to another endpoint."],
        },
        {
            "name": "List the webhook event types",
            "method": "GET",
            "path": f"{API_ROOT}/mie/v1/webhooks/event-types/",
            "url": f"{prefix}/mie/v1/webhooks/event-types/",
            "auth": auth,
            "rate_limit": "None",
            "purpose": "Every event type you can put in an endpoint's `events`, with what makes it fire.",
            "success_status": 200,
            "response_example": [
                {
                    "event": WebhookEventType.SUBMISSION_APPROVED.value,
                    "label": WebhookEventType.SUBMISSION_APPROVED.label,
                    "fires_when": WEBHOOK_EVENT_DOCS[WebhookEventType.SUBMISSION_APPROVED]["fires_when"],
                }
            ],
            "errors": [unauthorised],
        },
    ]


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
                "webhook_url": (
                    "string, required, HTTPS URL that will receive signed "
                    "events. It becomes your first webhook endpoint, taking "
                    "every event; add more and choose their events once approved."
                ),
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
                "webhook_endpoints": [SAMPLE_ALL_EVENTS_ENDPOINT],
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
                "confidence_note": (
                    "string, optional, up to "
                    f"{CONFIDENCE_NOTE_MAX_LENGTH} characters after "
                    "trimming. Your evidence that the idea has demand - "
                    "job-posting counts, search volume, community "
                    "questions. The one other key we read out of the body "
                    "into its own field, so reviewers see it beside the "
                    "title instead of hunting through the payload."
                ),
                "description": (
                    "string, optional, up to "
                    f"{DESCRIPTION_MAX_LENGTH} characters after trimming. "
                    "What the course would cover. Reviewers read it in the "
                    "idea's detail panel, so it is worth writing."
                ),
                "category": (
                    "string, optional. The platform category this belongs "
                    "to, by name or slug - 'Software Engineering' or "
                    "'software-engineering'. Matched case-insensitively "
                    "against live categories. A value we cannot match is "
                    "not an error: the idea is filed without a category and "
                    "your value stays in the payload for the reviewer."
                ),
                "difficulty_level": (
                    "string, optional, one of "
                    f"{', '.join(DifficultyLevel.values)}. How hard the "
                    "resulting course would be."
                ),
                "searches_per_month": (
                    "integer, optional, 0 or more. Monthly search volume "
                    "behind the idea. Shown beside the reviewer's demand "
                    "score, so send it when you have measured it."
                ),
                "<anything else>": (
                    "Optional. The entire JSON body is stored verbatim and "
                    "shown to reviewers - audience, outline, your own "
                    "internal ids, whatever helps the review."
                ),
            },
            "request_example": {
                "title": SAMPLE_TITLE,
                "description": "Systems programming for backend engineers",
                "category": "Software Engineering",
                "difficulty_level": DifficultyLevel.ADVANCED.value,
                "searches_per_month": 23000,
                "audience": "mid-level backend developers",
                "confidence_note": (
                    "620 backend job postings asked for Rust this month, up "
                    "28% on last month."
                ),
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
                {"status": 400, "when": "Missing title, empty title, title over 255 characters, a non-string or over-long confidence_note or description, an unknown difficulty_level, a negative or non-integer searches_per_month, or a non-object body."},
                {"status": 401, "when": "Missing, invalid, suspended, or inactive credentials."},
                {"status": 429, "when": "Ingest rate limit exceeded, or - for platform-owned accounts only - the rolling 24-hour submission cap. Either way, wait the seconds in Retry-After."},
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
            "name": "Course requirements",
            "method": "GET",
            "path": f"{API_ROOT}/mie/v1/course-requirements/",
            "url": f"{prefix}/mie/v1/course-requirements/",
            "auth": f"{API_KEY_HEADER} (or Bearer session token)",
            "rate_limit": "None",
            "purpose": (
                "Everything a course push is checked against, live: the "
                "structural limits, the active category and course version "
                "ids a push must reference, the allowed choice values, the "
                "payload caps, and the upload rules per media purpose."
            ),
            "success_status": 200,
            "response_example": {
                "structural_rules": {
                    "course_learning_objectives": {"min": 5, "max": 10},
                    "modules_per_course": {"min": 4, "max": 12},
                    "lessons_per_module": {"min": 3, "max": 8},
                    "lesson_learning_objectives": {"min": 2, "max": 5},
                    "text_lesson_script_words": {"min": 500, "max": 1500},
                    "course_description_words": {"min": 100, "max": 500},
                    "course_duration_minutes": {"min": 120, "max": 480},
                    "final_assessment_min_questions": 15,
                    "module_assessment_required": True,
                    "preview_video_required": True,
                    "course_version_required": True,
                },
                "categories": [
                    {
                        "id": SAMPLE_CATEGORY_ID,
                        "name": "Software Engineering",
                        "slug": "software-engineering",
                    }
                ],
                "course_versions": [{"id": SAMPLE_VERSION_ID, "label": "v1"}],
                "choices": {
                    "difficulty_level": DifficultyLevel.values,
                    "lesson_type": LessonContentType.values,
                    "content_block_type": ["HEADING_1", "PARAGRAPH", "IMAGE", "..."],
                    "question_type": QuestionType.values,
                },
                "quiz_rules": {
                    "choice_options": {
                        "min": MINIMUM_CHOICE_OPTIONS,
                        "max": MAXIMUM_CHOICE_OPTIONS,
                    }
                },
                "payload_limits": {
                    "modules": MAX_PUSH_MODULES,
                    "lessons_per_module": MAX_PUSH_LESSONS_PER_MODULE,
                    "content_blocks_per_lesson": MAX_PUSH_BLOCKS_PER_LESSON,
                    "media_url_max_length": COURSE_MEDIA_URL_MAX_LENGTH,
                    "request_body_max_bytes": settings.DATA_UPLOAD_MAX_MEMORY_SIZE,
                },
                "upload_purposes": ["<one entry per purpose - see media>"],
            },
            "errors": [
                {"status": 401, "when": "Missing, invalid, suspended, or inactive credentials."},
            ],
            "notes": [
                "The structural numbers in the example are illustrative. Read them from this endpoint - admins can change them without notice.",
                "Categories and versions are the only valid values for `category` and `version` in a push.",
            ],
        },
        {
            "name": "Upload course media to our storage",
            "method": "POST",
            "path": f"{API_ROOT}/mie/v1/uploads/presign/",
            "url": f"{prefix}/mie/v1/uploads/presign/",
            "auth": f"{API_KEY_HEADER} (or Bearer session token)",
            "rate_limit": _rate("mie_upload"),
            "purpose": (
                "Optional. A signed URL to PUT one media file into our "
                "storage, and the durable `media_url` to reference it by in "
                "a course push."
            ),
            "request_body": {
                "filename": "string, required. The file name with its extension, e.g. 'preview.mp4'.",
                "content_type": "string, required. The file's MIME type; must be allowed for the purpose.",
                "purpose": f"string, required, one of {list(MIE_UPLOAD_PURPOSES)}. Selects the rules.",
                "size": "integer, required. Exact size in bytes; signed into the upload.",
                "width": "integer. Required for videos and thumbnails.",
                "height": "integer. Required for videos and thumbnails.",
                "codec": "string. Required for videos; must be the purpose's codec.",
                "duration_seconds": "integer. Required for COURSE_PREVIEW_VIDEO.",
            },
            "request_example": {
                "filename": "preview.mp4",
                "content_type": "video/mp4",
                "purpose": "COURSE_PREVIEW_VIDEO",
                "size": 48000000,
                "width": 1920,
                "height": 1080,
                "codec": "h264",
                "duration_seconds": 90,
            },
            "success_status": 200,
            "response_example": {
                "upload_url": "https://<storage-host>/uploads/courses/9f1c...mp4?X-Amz-Signature=...",
                "upload_headers": {
                    "Content-Type": "video/mp4",
                    "x-amz-meta-upload-purpose": "COURSE_PREVIEW_VIDEO",
                    "x-amz-meta-width": "1920",
                    "x-amz-meta-height": "1080",
                    "x-amz-meta-duration-seconds": "90",
                    "x-amz-meta-codec": "h264",
                },
                "file_url": "https://<storage-host>/uploads/courses/9f1c...mp4?X-Amz-Expires=600&...",
                "file_key": "uploads/courses/9f1c2e7a4b5d4c3e8f9a0b1c2d3e4f5a.mp4",
                "media_url": "https://<storage-host>/uploads/courses/9f1c2e7a4b5d4c3e8f9a0b1c2d3e4f5a.mp4",
                "expires_in": 600,
            },
            "response_fields": {
                "upload_url": "PUT the raw file bytes here.",
                "upload_headers": "Send every one of these headers with the PUT, unchanged.",
                "file_url": "Temporary read link. Do not store it.",
                "file_key": "Storage key of the object.",
                "media_url": "Durable. This is what goes into your course push.",
                "expires_in": "Seconds before upload_url stops working.",
            },
            "errors": [
                {"status": 400, "when": "The file breaks a rule for its purpose - type, extension, size, resolution, aspect ratio, codec or duration - or a required field is missing."},
                {"status": 401, "when": "Missing, invalid, suspended, or inactive credentials."},
                {"status": 429, "when": "Upload rate limit exceeded. Wait the seconds in Retry-After."},
            ],
            "notes": [
                "Using our storage is optional - any HTTPS URL works in a push.",
                "Declare the real size and media properties. They are signed into the upload and recorded for QA.",
            ],
        },
        {
            "name": "Push the course for an approved idea",
            "method": "POST",
            "path": f"{API_ROOT}/mie/v1/submissions/<submission_id>/course/",
            "url": f"{prefix}/mie/v1/submissions/{SAMPLE_SUBMISSION_ID}/course/",
            "auth": f"{API_KEY_HEADER} (or Bearer session token)",
            "rate_limit": _rate("mie_course_push"),
            "purpose": (
                "Build the course for one of your approved ideas and submit "
                "it for review, in one all-or-nothing request. Also how you "
                "send a revision after a reviewer returns the course."
            ),
            "path_parameters": {
                "submission_id": "UUID of your approved idea - the `id` from the submit response or your queue.",
            },
            "request_body": {
                "course fields": "title, description, category, version, difficulty_level, preview_video_url, thumbnail_url, learning_objectives, tags, duration_*, terms_accepted - see course_schema.course.",
                "modules": "The module tree - see course_schema.module, .lesson, .content_block.",
                "final_assessment": "The final exam - see course_schema.assessment and .question.",
            },
            "request_example": SAMPLE_COURSE_PUSH,
            "success_status": 201,
            "response_example": {
                "submission_id": SAMPLE_SUBMISSION_ID,
                "submission_reference": _sample_reference(SubmissionStatus.APPROVED),
                "course_id": SAMPLE_COURSE_ID,
                "title": SAMPLE_TITLE,
                "status": CourseStatus.SUBMITTED.value,
                "module_count": 6,
                "lesson_count": 30,
                "submitted_at": "2026-09-30T10:00:00Z",
                "rejected_at": None,
                "published_at": None,
                "revision_feedback": None,
            },
            "response_fields": {
                "submission_id": "Your idea's id.",
                "submission_reference": "Your idea's public reference.",
                "course_id": "The course's id. Stable across revisions.",
                "status": "The course's status - SUBMITTED after a successful push.",
                "module_count / lesson_count": "What was built.",
                "submitted_at / rejected_at / published_at": "Timestamps of the latest submit, rejection and publication.",
                "revision_feedback": "Null here; filled while the course is DRAFT after a rejection.",
            },
            "errors": [
                {"status": 400, "when": "A field is invalid (reported by field path), the title or category does not match the idea, terms_accepted is not true, or the course fails the structural check (reported under structural_standards). Nothing was stored."},
                {"status": 401, "when": "Missing, invalid, suspended, or inactive credentials."},
                {"status": 404, "when": "The idea does not exist or is not yours."},
                {"status": 409, "when": "idea_not_approved - the idea is not APPROVED; course_in_review - its course is in review or published."},
                {"status": 429, "when": "Course push rate limit exceeded. Wait the seconds in Retry-After."},
            ],
            "notes": [
                "All or nothing: a failed push leaves no trace on our side.",
                "Every accepted push fires COURSE_SUBMITTED and returns 201 - the first one and every revision.",
                "A revision replaces the whole course. Always send all of it.",
                f"The request body may be up to {settings.DATA_UPLOAD_MAX_MEMORY_SIZE // (1024 * 1024)} MB - far more than the largest course the structural limits allow.",
            ],
        },
        {
            "name": "Check the course for an approved idea",
            "method": "GET",
            "path": f"{API_ROOT}/mie/v1/submissions/<submission_id>/course/",
            "url": f"{prefix}/mie/v1/submissions/{SAMPLE_SUBMISSION_ID}/course/",
            "auth": f"{API_KEY_HEADER} (or Bearer session token)",
            "rate_limit": "None",
            "purpose": (
                "Where your course stands in review, and - while it is back "
                "in DRAFT - the reviewer's feedback. Your reconciliation "
                "path for missed COURSE_* webhooks."
            ),
            "path_parameters": {
                "submission_id": "UUID of your idea.",
            },
            "success_status": 200,
            "response_example": {
                "submission_id": SAMPLE_SUBMISSION_ID,
                "submission_reference": _sample_reference(SubmissionStatus.APPROVED),
                "course_id": SAMPLE_COURSE_ID,
                "title": SAMPLE_TITLE,
                "status": CourseStatus.DRAFT.value,
                "module_count": 6,
                "lesson_count": 30,
                "submitted_at": "2026-09-01T10:00:00Z",
                "rejected_at": "2026-09-02T11:20:00Z",
                "published_at": None,
                "revision_feedback": SAMPLE_REVISION_FEEDBACK,
            },
            "errors": [
                {"status": 401, "when": "Missing, invalid, suspended, or inactive credentials."},
                {"status": 404, "when": "The idea is not yours, or no course has been pushed for it yet."},
            ],
            "notes": [
                "revision_feedback is null unless the course is DRAFT after a rejection.",
                "Status meanings are in course_lifecycle.",
            ],
        },
        {
            "name": "Send the video for a course whose text was approved",
            "method": "POST",
            "path": f"{API_ROOT}/mie/v1/submissions/<submission_id>/course/video/",
            "url": f"{prefix}/mie/v1/submissions/{SAMPLE_SUBMISSION_ID}/course/video/",
            "auth": f"{API_KEY_HEADER} (or Bearer session token)",
            "rate_limit": _rate("mie_course_push"),
            "purpose": (
                "Attach the preview video and each video lesson's media, and "
                "send the course to the video review seat. Only when the "
                "platform reviews text before video, after COURSE_TEXT_APPROVED."
            ),
            "request_body": {
                "preview_video_url": "string, required, HTTPS URL of the 1-2 minute preview",
                "lessons": (
                    "array, optional: {module_order, lesson_order, video_url | "
                    "embedded_link} for each VIDEO lesson, addressed by the "
                    "orders of your text push"
                ),
            },
            "request_example": {
                "preview_video_url": "https://cdn.studio.io/rust/preview.mp4",
                "lessons": [
                    {
                        "module_order": 1,
                        "lesson_order": 1,
                        "video_url": "https://cdn.studio.io/rust/m1l1.mp4",
                    }
                ],
            },
            "success_status": 200,
            "errors": [
                {"status": 400, "when": "A field is invalid, a lesson does not exist, or the course fails the structural check. Nothing is stored."},
                {"status": 401, "when": "Missing, invalid, suspended, or inactive credentials."},
                {"status": 404, "when": "The idea is not yours."},
                {"status": 409, "when": "course_not_awaiting_video: the course is not waiting for its video. video_provider_conflict: the platform supplies this video."},
            ],
            "notes": ["COURSE_SUBMITTED fires on success."],
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
                "webhook_endpoints": [SAMPLE_ALL_EVENTS_ENDPOINT],
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
        *_webhook_endpoint_routes(prefix),
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
        "your_endpoints": _endpoint_summaries(account),
        "endpoints_and_subscriptions": {
            "summary": (
                "You can receive webhooks on up to "
                f"{MAX_WEBHOOK_ENDPOINTS} endpoints. Each one takes every "
                f'event (`events: ["{WEBHOOK_ALL_EVENTS}"]`, including types '
                "added later) or only the event types you list. Manage them "
                f"at {API_ROOT}/mie/v1/webhooks/ with your API key, or from "
                "your developer profile; change them as often as you like."
            ),
            "your_first_endpoint": (
                "The URL you registered with is your first endpoint and takes "
                "every event. Narrow it, change its URL, or add others."
            ),
            "delivery_per_endpoint": (
                "Each event is delivered separately to every endpoint that "
                "takes it, with its own retries: one endpoint being down never "
                "delays another. Each endpoint receives its own `event_id` for "
                "the occurrence, so dedupe per endpoint."
            ),
            "when_changes_apply": (
                "To events recorded after the change. Deliveries already "
                "queued for an endpoint still go to it, at its current URL."
            ),
            "deleting": (
                "Deleting an endpoint drops its queued deliveries. Your last "
                "endpoint cannot be deleted (409 `last_webhook_endpoint`); "
                "change its URL or events instead."
            ),
            "signing": (
                "Every endpoint is signed with the same signing secret, from "
                f"{API_ROOT}/mie/v1/me/."
            ),
            "event_types": f"GET {API_ROOT}/mie/v1/webhooks/event-types/ lists every type you can choose.",
        },
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
                "submission": (
                    "Object describing the submission at that moment. On "
                    "COURSE_* events it also carries `course` - the course's "
                    "id, status and title, plus `revision_feedback` on "
                    "COURSE_REVISION_REQUESTED."
                ),
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
            "course_events": (
                "Idea and course events share the submission. Track the "
                "idea's state from SUBMISSION_* events and the course's "
                "state from COURSE_* events separately - a COURSE_PUBLISHED "
                "does not change the idea's status, and a SUBMISSION_REJECTED "
                "does not change the course's."
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
            "Returns 2xx for event types it does not recognise. New types are added as the API grows; an unknown type must never fail the delivery.",
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
        if "course" in docs["extra_fields"]:
            submission["course"] = {
                "id": SAMPLE_COURSE_ID,
                "status": COURSE_STATUS_BY_EVENT[event_type].value,
                "title": SAMPLE_TITLE,
            }
        if "revision_feedback" in docs.get("extra_course_fields", []):
            submission["course"]["revision_feedback"] = SAMPLE_REVISION_FEEDBACK

        catalogue.append(
            {
                "type": event_type.value,
                "label": event_type.label,
                "fires_when": docs["fires_when"],
                "resulting_status": status.value if status else None,
                "status_unchanged": status is None,
                "extra_submission_fields": docs["extra_fields"],
                "extra_course_fields": docs.get("extra_course_fields", []),
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
                "when": (
                    "The request body is malformed or fails field validation. "
                    "On a course push this also covers a title or category "
                    "that does not match the idea, and the structural check "
                    "at submit - one error per failed rule, each with "
                    "field_name `structural_standards`. Nested field errors "
                    "name their path, e.g. `modules.0.lessons.2.title`."
                ),
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
                "status": 409,
                "type": "client_error",
                "when": (
                    "The request conflicts with current state. On the course "
                    "push: `idea_not_approved` - the idea is not APPROVED; "
                    "`course_in_review` - its course is in review or "
                    "published and cannot be replaced until a reviewer "
                    "sends it back."
                ),
                "retry": "Not until the state changes - wait for the matching webhook.",
                "example": {
                    "errors": [
                        {
                            "type": "client_error",
                            "code": "idea_not_approved",
                            "message": "A course can only be pushed for an approved idea. This idea is PENDING_REVIEW.",
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
            "create a second submission. A course push is safe to retry "
            "after a timeout: it either did nothing, or it succeeded and the "
            "retry gets 409 course_in_review - check GET .../course/."
        ),
    }


def _rate_limits() -> dict:
    return {
        "scope": (
            "Registration is limited per client IP, because no account "
            "exists yet. Every other limit below is per developer account: "
            "one bucket whether you authenticate with your API key or a "
            "platform session, and never shared with another account "
            "behind the same IP. Endpoints not listed here are not rate "
            "limited."
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
            {
                "endpoint": f"POST {API_ROOT}/mie/v1/submissions/<submission_id>/course/",
                "limit": _rate("mie_course_push"),
                "why": "Each push builds and submits a whole course in one transaction.",
                "advice": (
                    "A 400 still counts. Validate against "
                    "/mie/v1/course-requirements/ before pushing rather than "
                    "iterating against the endpoint."
                ),
            },
            {
                "endpoint": f"POST {API_ROOT}/mie/v1/uploads/presign/",
                "limit": _rate("mie_upload"),
                "why": "Sized for a full course with a video on every lesson.",
                "advice": "Presign when you are ready to PUT; an unused URL still counts.",
            },
            {
                "endpoint": f"POST {API_ROOT}/mie/v1/submissions/",
                "limit": (
                    f"{settings.MIE_SYSTEM_DAILY_SUBMISSION_CAP} per rolling "
                    "24 hours - platform-owned accounts only"
                ),
                "why": (
                    "A daily ceiling on the platform's own crawler, so a "
                    "broken run cannot bury the review queue. Third-party "
                    "developer accounts are never subject to it."
                ),
                "advice": (
                    "Counted in the database over every outcome, dedup "
                    "short-circuits included, so resubmitting duplicates "
                    "spends the allowance too. Retry-After says when the "
                    "oldest counted submission leaves the window."
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
        {
            "item": "Course pushes are only sent after SUBMISSION_APPROVED",
            "why": "Anything earlier is a 409 and counts against the push rate limit.",
        },
        {
            "item": "The course title is the idea's title, byte for byte in intent",
            "why": "A reworded title is a 400; the idea's title is the key.",
        },
        {
            "item": "Category and version ids are read from /course-requirements/ at run time",
            "why": "They are the only valid values, and admins can retire them.",
        },
        {
            "item": "Every revision push sends the complete course",
            "why": "A push replaces the course; anything left out is deleted.",
        },
        {
            "item": "Media URLs stay reachable for the life of the course",
            "why": "Reviewers and learners play them from where you put them; our storage keeps them for you.",
        },
        {
            "item": "COURSE_REVISION_REQUESTED and COURSE_PUBLISHED are handled",
            "why": "They are the only signals that you need to act, or that you are done.",
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
                "Yes. PATCH /mie/v1/webhooks/{id}/ with a new `url`, from your "
                "code or your developer profile. You can also add more "
                "endpoints and choose which events each one receives."
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
            "question": "Can I push a course before my idea is approved?",
            "answer": (
                "No. The push checks the idea first and returns 409 "
                "idea_not_approved for anything but APPROVED. Wait for "
                "SUBMISSION_APPROVED."
            ),
        },
        {
            "question": "Does my course need videos?",
            "answer": (
                "Only the 60-120 second preview video. Lessons can be text "
                "only: use lesson_type TEXT with a script. A VIDEO lesson "
                "needs a video_url or embedded_link."
            ),
        },
        {
            "question": "Can I host videos on YouTube, Vimeo or my own CDN?",
            "answer": (
                "Yes. Every media field takes any HTTPS URL, and "
                "embedded_link takes a player's embed link. If you would "
                "rather we host them, use POST /mie/v1/uploads/presign/."
            ),
        },
        {
            "question": "My push came back 400 with a long list. Was anything saved?",
            "answer": (
                "No. A push is all or nothing. Fix every item in the list - "
                "field errors by path, submission rules under "
                "structural_standards - and push again."
            ),
        },
        {
            "question": "A reviewer rejected my course. What do I do?",
            "answer": (
                "Read revision_feedback on the COURSE_REVISION_REQUESTED "
                "webhook, or on GET /mie/v1/submissions/<id>/course/. Fix "
                "the course and push the complete course again to the same "
                "idea. It goes back into review from the first seat."
            ),
        },
        {
            "question": "Can I change my course while it is in review?",
            "answer": (
                "No. From submission until a reviewer sends it back, a push "
                "returns 409 course_in_review. After publication it can "
                "never be replaced."
            ),
        },
        {
            "question": "Can I use a different title for the course?",
            "answer": (
                "No. The course is the idea that was approved, so it carries "
                "the idea's title. Differences in case and surrounding "
                "spaces are fine."
            ),
        },
        {
            "question": "Why is topic not accepted?",
            "answer": (
                "A course's topic is reserved by its creator from the "
                "creator dashboard. A pushed course is filed under its "
                "idea's category and priced by category and difficulty."
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


def _course_upload(account, base_url: str) -> dict:
    """The complete guide to pushing a course for an approved idea."""

    key_example = (
        f"{account.api_key_prefix}..." if account.api_key_prefix else f"{API_KEY_PREFIX}..."
    )
    prefix = f"{base_url}{API_ROOT}"
    push_path = f"{API_ROOT}/mie/v1/submissions/<submission_id>/course/"
    return {
        "summary": (
            "Once an idea is APPROVED, you write the course yourself and push "
            "it to us in one request, in the same schema our course builder "
            "uses. From that moment it is an ordinary course on the "
            "platform: it goes through content review, then QA "
            "verification, then publication - exactly the path a course "
            "written by one of our own creators takes. The only difference "
            "is that it reached us over the API."
        ),
        "when_you_can_push": {
            "rule": (
                "Only for your own idea, and only while that idea is "
                f"{SubmissionStatus.APPROVED.value}. We check the idea's "
                "status before we read the rest of your request."
            ),
            "not_yet_approved": (
                f"{SubmissionStatus.PENDING_REVIEW.value}, "
                f"{SubmissionStatus.REJECTED.value} and every dedup outcome "
                "return 409 idea_not_approved. Nothing is stored."
            ),
            "not_yours": (
                "An idea id that is not yours returns 404 - the same as one "
                "that does not exist."
            ),
            "reversals": (
                "If an admin reverses the approval after you pushed, your "
                "course is left exactly where it is in review, but you "
                "cannot push a revision until the idea is approved again."
            ),
        },
        "matching_rules": [
            {
                "field": "title",
                "rule": (
                    "Must be the approved idea's title, compared the way "
                    "dedup compares titles: trimmed, case-insensitive. The "
                    "course is stored under the idea's title exactly as the "
                    "idea has it."
                ),
                "on_mismatch": "400 on `title`, with the expected title in the message.",
            },
            {
                "field": "category",
                "rule": (
                    "An active category id from GET /mie/v1/course-requirements/. "
                    "When your idea was filed under a category, it must be "
                    "that category."
                ),
                "on_mismatch": "400 on `category`.",
            },
            {
                "field": "terms_accepted",
                "rule": (
                    "Must be true: you accept the category's Terms and "
                    "Conditions for this course, as a creator does."
                ),
                "on_mismatch": "400 on `terms_accepted`.",
            },
        ],
        "one_course_per_idea": (
            "Each approved idea has exactly one course. The first accepted "
            "push creates it; every later accepted push replaces that same "
            "course. A different idea is a different course."
        ),
        "all_or_nothing": (
            "A push is one transaction. Your course is built, checked "
            "against every structural rule, and submitted for review - or, "
            "if anything fails, nothing at all is stored and every failure "
            "comes back in one 400 so you can fix them together. There is "
            "never a half-built course on our side."
        ),
        "what_submit_checks": (
            "The same structural check a creator's course faces when they "
            "press Submit: learning objective counts, module and lesson "
            "counts, script length for TEXT lessons, description length, "
            "total duration, a preview video, a course version, accepted "
            "terms, a quiz on every module and a final assessment with "
            "enough questions. The limits are tunable by our admins, so read "
            "them live from GET /mie/v1/course-requirements/ rather than "
            "hard-coding them. Failures come back under the "
            "`structural_standards` field."
        ),
        "revisions": {
            "how_it_works": (
                "When a reviewer - in content review or in QA verification - "
                "sends your course back, it returns to DRAFT and "
                f"{WebhookEventType.COURSE_REVISION_REQUESTED.value} fires "
                "with the reviewer's feedback and every issue they flagged, "
                "named by module and lesson title. Fix the course and push "
                "the whole thing again to the same idea."
            ),
            "replacement_is_total": (
                "A revision push replaces the course entirely: every course "
                "field, every module, lesson, block and quiz. Always send "
                "the complete course, never only the parts that changed."
            ),
            "while_in_review": (
                "While the course is SUBMITTED, IN_REVIEW, QA_VERIFICATION, "
                "APPROVED or PUBLISHED, a push returns 409 course_in_review. "
                "Wait for the revision webhook."
            ),
            "history": (
                "Reviewer feedback from earlier rounds is kept on our side; "
                "replacing the content does not erase it."
            ),
        },
        "ownership": (
            "On your first push we create a platform creator account that "
            "owns your courses on our side. You never sign in to it and "
            "never need to know it exists - it cannot sign in at all. It is "
            "what lets your course run through the same review, QA and "
            "publishing tools as every other course."
        ),
        "not_accepted": [
            "`topic` - a pushed course is placed by its idea's category and "
            "priced by category and difficulty.",
            "QUIZ content blocks - put a lesson's quiz in the lesson's "
            "`assessment` instead.",
            "Any id, owner or status field - we assign those.",
        ],
        "steps": [
            {
                "step": 1,
                "title": "Wait for the approval",
                "detail": (
                    f"Handle {WebhookEventType.SUBMISSION_APPROVED.value}. "
                    "Keep the submission `id` - it is the path parameter for "
                    "every course call."
                ),
                "curl": None,
            },
            {
                "step": 2,
                "title": "Read the live requirements",
                "detail": (
                    "Pick a category id and a course version id, and read "
                    "the current structural limits and upload rules."
                ),
                "curl": (
                    f"curl -sS {prefix}/mie/v1/course-requirements/ \\\n"
                    f'  -H "{API_KEY_HEADER}: {key_example}"'
                ),
            },
            {
                "step": 3,
                "title": "Put your media somewhere reachable (optional)",
                "detail": (
                    "Use any HTTPS URL you already have, or upload to our "
                    "storage: presign, PUT the bytes, keep `media_url`. See "
                    "the `media` section."
                ),
                "curl": (
                    f"curl -sS -X POST {prefix}/mie/v1/uploads/presign/ \\\n"
                    f'  -H "{API_KEY_HEADER}: {key_example}" \\\n'
                    '  -H "Content-Type: application/json" \\\n'
                    "  -d '{\"filename\": \"preview.mp4\", \"content_type\": \"video/mp4\", "
                    "\"purpose\": \"COURSE_PREVIEW_VIDEO\", \"size\": 48000000, "
                    "\"width\": 1920, \"height\": 1080, \"codec\": \"h264\", "
                    "\"duration_seconds\": 90}'"
                ),
            },
            {
                "step": 4,
                "title": "Push the course",
                "detail": (
                    "One POST with the whole course. 201 means built and "
                    "submitted for review. Keep `course_id`."
                ),
                "curl": (
                    f"curl -sS -X POST {prefix}/mie/v1/submissions/"
                    f"{SAMPLE_SUBMISSION_ID}/course/ \\\n"
                    f'  -H "{API_KEY_HEADER}: {key_example}" \\\n'
                    '  -H "Content-Type: application/json" \\\n'
                    "  --data @course.json"
                ),
            },
            {
                "step": 5,
                "title": "Fix and resend on a 400",
                "detail": (
                    "Nothing was stored. Every problem is listed - field "
                    "errors by field path, submission rules under "
                    "`structural_standards`. Fix them all and push again."
                ),
                "curl": None,
            },
            {
                "step": 6,
                "title": "Follow it through review",
                "detail": (
                    f"Handle {WebhookEventType.COURSE_REVISION_REQUESTED.value} "
                    "(fix and push again) and "
                    f"{WebhookEventType.COURSE_PUBLISHED.value} (done). "
                    f"GET {push_path} shows the current state at any time."
                ),
                "curl": (
                    f"curl -sS {prefix}/mie/v1/submissions/"
                    f"{SAMPLE_SUBMISSION_ID}/course/ \\\n"
                    f'  -H "{API_KEY_HEADER}: {key_example}"'
                ),
            },
        ],
        "common_mistakes": [
            "Pushing before SUBMISSION_APPROVED. It returns 409 and stores nothing.",
            "Rewording the title. It must be the idea's title - fix typos in "
            "the idea before it is approved, not in the course.",
            "Sending only the changed module on a revision. The push "
            "replaces the whole course; anything you leave out is gone.",
            "Leaving out `duration_minutes` on lessons. The course's total "
            "duration is the sum of its lessons, and it is checked.",
            "Storing `file_url` from a presign. It expires in minutes; store "
            "`media_url`.",
            "Marking a lesson VIDEO without a `video_url` or "
            "`embedded_link`. Use TEXT for a lesson without video.",
        ],
    }


def _course_schema() -> dict:
    """Field-by-field reference for the push body."""

    question_types = ", ".join(QuestionType.values)
    return {
        "summary": (
            "The push body is the course builder's own schema, nested: the "
            "course's fields at the top level, `modules` holding "
            "`lessons`, each lesson holding optional `content_blocks` and an "
            "optional quiz, each module holding its quiz, and a "
            "`final_assessment`. Unknown keys are ignored."
        ),
        "limits_note": (
            "Numeric limits in the structural rules (word counts, how many "
            "modules, lessons, objectives, questions, total minutes) are "
            "tunable by our admins and are served live by GET "
            "/mie/v1/course-requirements/. The limits listed here are the "
            "fixed ones."
        ),
        "course": {
            "title": "string, required. Must be the approved idea's title (trimmed, case-insensitive).",
            "description": (
                "string, required. What the course teaches and who it is "
                "for. Word count is checked at submit."
            ),
            "category": "UUID, required. An active category id; the idea's category if it has one.",
            "version": "UUID, required at submit. An active course version id from the requirements endpoint.",
            "difficulty_level": f"string, optional, one of {', '.join(DifficultyLevel.values)}. Sets the category price used.",
            "preview_video_url": (
                "URL, required at submit. A 60-120 second overview video "
                f"(BR-015). Up to {COURSE_MEDIA_URL_MAX_LENGTH} characters."
            ),
            "thumbnail_url": f"URL, optional. The course cover image. Up to {COURSE_MEDIA_URL_MAX_LENGTH} characters.",
            "learning_objectives": "list of non-empty strings. Count is checked at submit.",
            "tags": "list of non-empty strings, optional.",
            "duration_hours / duration_minutes / duration_seconds": (
                "integers >= 0, optional. Your planned length, for display. "
                "The duration checked at submit is the sum of the lessons' "
                "duration_minutes, not this."
            ),
            "terms_accepted": "boolean, required, must be true.",
            "modules": f"list, required, 1 to {MAX_PUSH_MODULES} items. See `module`.",
            "final_assessment": "object, required at submit. See `assessment`; needs the minimum question count.",
        },
        "module": {
            "title": "string, required.",
            "order": (
                "integer, optional. 1-based position; defaults to the list "
                "position. Must be unique within the course."
            ),
            "description": "string, optional.",
            "learning_objectives": "list of non-empty strings.",
            "lessons": (
                f"list, required, 1 to {MAX_PUSH_LESSONS_PER_MODULE} items. "
                "See `lesson`. The per-module count is checked at submit."
            ),
            "assessment": "object, required at submit. The module quiz. See `assessment`.",
        },
        "lesson": {
            "title": "string, required.",
            "order": "integer, optional. 1-based; defaults to the list position; unique within the module.",
            "lesson_type": (
                f"string, one of {', '.join(LessonContentType.values)}; "
                f"defaults to {LessonContentType.TEXT.value}. VIDEO requires "
                "`video_url` or `embedded_link`. TEXT lessons have their "
                "`script` word count checked at submit."
            ),
            "script": "string. The lesson's text or narration. Required in practice for TEXT lessons.",
            "video_url": f"URL, optional. The lesson video, on any HTTPS host or our storage. Up to {COURSE_MEDIA_URL_MAX_LENGTH} characters.",
            "embedded_link": "URL, optional. An embeddable player link (YouTube, Vimeo, Wistia...).",
            "video_script_file": "string, optional. A subtitle (.srt) file URL or storage key.",
            "learning_objectives": "list of non-empty strings. Count is checked at submit.",
            "duration_minutes": "integer >= 0. Summed into the course duration checked at submit.",
            "lesson_requirement": (
                "string, optional. What the learner needs before this "
                "lesson. Send this or `requirements`, not both."
            ),
            "requirements": "list of {text, order}, optional. The same, as separate lines.",
            "content_blocks": f"list, optional, up to {MAX_PUSH_BLOCKS_PER_LESSON} items. See `content_block`.",
            "assessment": "object, optional. A lesson quiz; no question minimum.",
        },
        "content_block": {
            "order": "integer, optional. 1-based; defaults to the list position; unique within the lesson.",
            "block_type": (
                "string, required. Text blocks (HEADING_1, HEADING_2, "
                "PARAGRAPH, NUMBERED_LIST, BULLETED_LIST, BLOCKQUOTE) need "
                "`text_content`; media blocks (IMAGE, VIDEO, EMBED) need "
                "`media_url`; DIVIDER carries nothing. QUIZ is not accepted."
            ),
            "text_content": "string. Only on text blocks.",
            "media_url": f"string. Only on media blocks; any URL or our `media_url`. Up to {COURSE_MEDIA_URL_MAX_LENGTH} characters.",
        },
        "assessment": {
            "title": "string, required, up to 255 characters.",
            "questions": "list of questions, required. See `question`.",
        },
        "question": {
            "type": f"string, one of {question_types}; defaults to {QuestionType.MULTIPLE_CHOICE.value}.",
            "question": "string, required.",
            "points": "integer >= 0, optional, defaults to 0.",
            "options": (
                f"list of {MINIMUM_CHOICE_OPTIONS}-{MAXIMUM_CHOICE_OPTIONS} "
                "non-empty strings. Required for choice questions; not "
                "allowed on ESSAY."
            ),
            "correct_index": "integer, the 0-based correct option for SINGLE_CHOICE.",
            "correct_indices": "list of 0-based indexes for MULTIPLE_CHOICE.",
            "expected_answer": "string, required for ESSAY only.",
            "explanation": "string, optional. Shown after answering.",
        },
        "example": SAMPLE_COURSE_PUSH,
    }


def _media(base_url: str) -> dict:
    """Where course media can live, and how to use our storage."""

    return {
        "summary": (
            "Every media field in a push is a URL. Where the file lives is "
            "up to you: your own host or CDN, a video platform, or our "
            "storage. Video is optional per lesson; the course preview "
            "video is the one media item every course needs."
        ),
        "what_needs_media": [
            {
                "item": "Course preview video (`preview_video_url`)",
                "required": True,
                "note": "60-120 seconds (BR-015). Checked at submit.",
            },
            {
                "item": "Course thumbnail (`thumbnail_url`)",
                "required": False,
                "note": "Recommended - it is the course's cover image.",
            },
            {
                "item": "Lesson video (`video_url` or `embedded_link`)",
                "required": False,
                "note": (
                    "Only a VIDEO lesson needs one. A TEXT lesson is text "
                    "only, so a course can be entirely text apart from its "
                    "preview video."
                ),
            },
            {
                "item": "Images and video inside the lesson body (`content_blocks[].media_url`)",
                "required": False,
                "note": "IMAGE, VIDEO and EMBED blocks each need one.",
            },
        ],
        "hosting_options": [
            {
                "option": "Your own host or CDN",
                "how": (
                    "Put the file's HTTPS URL straight into the field. It "
                    "must stay reachable for as long as the course is live - "
                    "reviewers watch it, and learners will."
                ),
            },
            {
                "option": "A video platform",
                "how": (
                    "Use the platform's embeddable link in a lesson's "
                    "`embedded_link` or an EMBED block, or its watch URL in "
                    "`video_url`."
                ),
            },
            {
                "option": "Our storage",
                "how": (
                    "Presign, PUT the bytes, then use the returned "
                    "`media_url`. The files are private; the platform issues "
                    "short-lived playback URLs from them."
                ),
            },
        ],
        "our_storage": {
            "endpoint": f"POST {API_ROOT}/mie/v1/uploads/presign/",
            "steps": [
                "POST the file's name, content_type, purpose, size in bytes and - "
                "for videos and thumbnails - width, height and codec "
                "(preview videos also duration_seconds).",
                "PUT the raw file bytes to `upload_url`, sending every header "
                "in `upload_headers` exactly as given, before `expires_in` "
                "seconds pass. The declared size is signed into the upload.",
                "On a 2xx from storage, put `media_url` into your course push.",
            ],
            "put_example": (
                "curl -sS -X PUT '<upload_url>' \\\n"
                "  -H 'Content-Type: video/mp4' \\\n"
                "  -H 'x-amz-meta-upload-purpose: COURSE_PREVIEW_VIDEO' \\\n"
                "  -H 'x-amz-meta-width: 1920' \\\n"
                "  -H 'x-amz-meta-height: 1080' \\\n"
                "  -H 'x-amz-meta-duration-seconds: 90' \\\n"
                "  -H 'x-amz-meta-codec: h264' \\\n"
                "  --data-binary @preview.mp4"
            ),
            "size_must_match": (
                "The `size` you declared is signed into the upload as its "
                "Content-Length, which your HTTP client sets from the body. "
                "If the file is not exactly that many bytes, storage "
                "rejects the PUT - presign again with the real size."
            ),
            "keep": (
                "`media_url` - durable. Not `file_url`: that is a temporary "
                "read link that expires with the upload URL."
            ),
            "rules_per_purpose": [
                {
                    "purpose": purpose,
                    "content_types": sorted(COURSE_UPLOAD_RULES[purpose]["content_types"]),
                    "extensions": sorted(COURSE_UPLOAD_RULES[purpose]["extensions"]),
                    "max_size": _human_bytes(COURSE_UPLOAD_RULES[purpose]["max_size"]),
                    "min_resolution": (
                        f"{MIN_MEDIA_WIDTH}x{MIN_MEDIA_HEIGHT}"
                        if COURSE_UPLOAD_RULES[purpose].get("dimensions")
                        else None
                    ),
                    "aspect_ratio": (
                        "{}:{}".format(*COURSE_UPLOAD_RULES[purpose]["aspect_ratio"])
                        if "aspect_ratio" in COURSE_UPLOAD_RULES[purpose]
                        else None
                    ),
                    "codec": COURSE_UPLOAD_RULES[purpose].get("codec"),
                    "duration_seconds": (
                        "{}-{}".format(*COURSE_UPLOAD_RULES[purpose]["duration_range"])
                        if "duration_range" in COURSE_UPLOAD_RULES[purpose]
                        else None
                    ),
                }
                for purpose in MIE_UPLOAD_PURPOSES
            ],
            "external_media_note": (
                "These rules apply to files uploaded to our storage. Media "
                "you host yourself is not inspected on upload, but reviewers "
                "and QA verification hold it to the same standard - a "
                "blurry or broken video is a revision request."
            ),
        },
    }


def _course_lifecycle() -> dict:
    """Every status a pushed course can be in, from the developer's side."""

    return {
        "summary": (
            "Your idea and your course have separate statuses. The idea "
            "stays APPROVED; the course moves through review. Course moves "
            "arrive as COURSE_* webhooks, and GET "
            f"{API_ROOT}/mie/v1/submissions/<submission_id>/course/ always "
            "shows where it stands."
        ),
        "path": (
            "push -> SUBMITTED -> IN_REVIEW (content review seats) -> "
            "QA_VERIFICATION -> APPROVED -> PUBLISHED. A rejection at any "
            "review seat or in QA returns it to DRAFT, and your next push "
            "starts the review again from the first seat."
        ),
        "statuses": [
            {"status": status.value, "label": status.label, **COURSE_STATUS_DOCS[status]}
            for status in CourseStatus
        ],
        "events": [
            {
                "event": event_type.value,
                "course_status": COURSE_STATUS_BY_EVENT[event_type].value,
                "fires_when": WEBHOOK_EVENT_DOCS[event_type]["fires_when"],
            }
            for event_type in COURSE_STATUS_BY_EVENT
        ],
        "not_announced": (
            "Moves inside review (a reviewer claiming the course, passing a "
            "seat, QA approval) fire no webhook. Use GET .../course/ if you "
            "want to show finer progress."
        ),
    }


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


def _human_bytes(size: int) -> str:
    return f"{size // (1024 * 1024)} MB"
