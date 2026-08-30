# MIE (Market Intelligence Engine) — End-to-End Technical Reference

> **Scope**: This document is a deep-dive technical reference for developers
> and architects working on the MIE subsystem. It covers every component
> from data model to webhook delivery, grounded in the actual source code
> under `api/mie/`.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Data Model](#3-data-model)
4. [Authentication](#4-authentication)
5. [Developer Lifecycle](#5-developer-lifecycle)
6. [Ingest + Dedup Engine (Endpoint 1)](#6-ingest--dedup-engine-endpoint-1)
7. [Webhook Dispatcher](#7-webhook-dispatcher)
8. [Developer Surfaces](#8-developer-surfaces)
9. [Admin Console](#9-admin-console)
10. [Reference Scheme](#10-reference-scheme)
11. [Event Types](#11-event-types)
12. [Error Handling](#12-error-handling)
13. [File Map](#13-file-map)

---

## 1. Overview

MIE is the external-developer course-idea pipeline for Course Builder.
External developers register, get approved by a superadmin, submit course
ideas, and receive webhook notifications about every state change to their
submissions. Admins review submissions, approve or reject them, and the
system handles deduplication, payout tracking, and webhook delivery.

**Who it serves:**

- **External developers** — register via a public endpoint, receive an API
  key on approval, submit course ideas, and monitor status through a
  developer-facing queue API plus signed webhook events.
- **Platform superadmins** — manage the developer lifecycle (approve,
  reject, suspend), review and decide on submissions, set recommendation
  signals, and manage a rejection-reason taxonomy.

**High-level flow:**

```
Developer registers (PENDING)
        │
        ▼
Superadmin approves ──► API key issued (shown once)
        │
        ▼
Developer submits idea (POST /api/v1/mie/v1/submissions/)
        │
        ├── Dedup check 1: Previously rejected title?  → PREVIOUSLY_REJECTED
        ├── Dedup check 2: Existing course title?       → DUPLICATE_EXISTING
        ├── Dedup check 3: Title already in queue?      → DUPLICATE_IN_QUEUE
        └── No match                                   → PENDING_REVIEW
        │
        ▼
Webhook event recorded immediately for every outcome
        │
        ▼
Dispatcher sweeps every 60s → signed POST to developer's webhook_url
        │
        ▼
Superadmin decides (approve / reject) — reversible at any time
        │
        ▼
New webhook event fired on every decision flip
```

---

## 2. Architecture

### 2.1 App structure

MIE lives in `api/mie/` — a self-contained Django app with 30+ source
files (excluding `__pycache__` and migrations):

```
api/mie/
├── models/                  # 4 Django ORM models
│   ├── developer_account.py
│   ├── course_submission.py
│   ├── rejection_reason.py
│   └── webhook_event.py
├── services/                # Business logic layer (9 modules)
│   ├── key_service.py
│   ├── dev_token_service.py
│   ├── developer_service.py
│   ├── submission_service.py
│   ├── submission_admin_service.py
│   ├── dedup_service.py
│   ├── webhook_dispatcher.py
│   ├── documentation_service.py
│   └── reference.py
├── views/                   # DRF views (6 modules)
│   ├── dev_registration_views.py
│   ├── dev_submission_views.py
│   ├── dev_account_views.py
│   ├── admin_developer_views.py
│   ├── admin_submission_views.py
│   └── rejection_reason_views.py
├── serializers/             # DRF serializers (6 modules)
│   ├── submission_serializer.py
│   ├── dev_submission_serializer.py
│   ├── admin_submission_serializer.py
│   ├── developer_admin_serializer.py
│   ├── dev_me_serializer.py
│   └── rejection_reason_serializer.py
├── management/commands/
│   └── dispatch_mie_webhooks.py
├── migrations/
│   ├── 0001_initial.py
│   └── 0002_alter_webhookevent_event_type.py
├── authentication.py        # Dual-path DRF authentication class
├── permissions.py           # IsMieDeveloper permission
├── filters.py               # AdminSubmissionFilterSet (django-filters)
├── enums.py                 # All TextChoices enums
├── urls.py                  # Route registration (router + path)
├── admin.py                 # Django admin registrations
├── apps.py                  # AppConfig
└── tasks.py                 # Celery shared_task for beat scheduling
```

### 2.2 Service layer

All business logic lives in `services/`. Views are thin controllers that
delegate to service functions. No model instance methods contain business
rules — models are data-only.

| Service | Responsibility |
|---|---|
| `key_service` | Generate, hash, revoke, and authenticate API keys |
| `dev_token_service` | Issue and resolve PyJWT platform session tokens |
| `developer_service` | Registration, approval, rejection, suspension |
| `submission_service` | Ingest, validate payload, run dedup, record events |
| `submission_admin_service` | Approve/reject submissions, set signals, payout bypass |
| `dedup_service` | Three-stage title dedup engine |
| `webhook_dispatcher` | Record-then-sweep delivery with backoff |
| `documentation_service` | Build machine-readable docs from live constants |
| `reference` | Public reference suffix mapping |

### 2.3 Scheduling — no Celery broker dependency

MIE does not require a running Celery broker for core operation. The
webhook dispatcher is invoked via one of two mechanisms:

1. **Celery beat** — `dispatch_due_webhooks_task` in `tasks.py`, scheduled
   every 60 seconds via `CELERY_BEAT_SCHEDULE`.
2. **Management command** — `python manage.py dispatch_mie_webhooks`, callable
   from cron or any process scheduler.

Both call the same function: `webhook_dispatcher.dispatch_due_events()`.

---

## 3. Data Model

### 3.1 DeveloperAccount

**File**: `api/mie/models/developer_account.py`

Represents an external developer registered into the MIE pipeline.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto-generated via `UUIDPrimaryKeyModelMixin` |
| `email` | EmailField | **Unique.** Registration identity and login handle. |
| `webhook_url` | URLField | HTTPS endpoint receiving signed POSTs. |
| `status` | CharField(10) | `PENDING` / `APPROVED` / `REJECTED` / `SUSPENDED` |
| `plan_type` | CharField(25) | `PAID_PER_SUBMISSION` / `BYPASS_PER_SUBMISSION` / `BYPASS_ACCOUNT` |
| `api_key_prefix` | CharField(16) | First 16 chars of the raw key (`scb_live_...`). Non-secret. |
| `api_key_hash` | CharField(64) | SHA-256 hex digest of the full key. Raw key never stored. |
| `api_key_issued_at` | DateTimeField | When the current key was generated. |
| `api_key_last_used_at` | DateTimeField | Updated on every successful key auth. |
| `signing_secret` | CharField(64) | HMAC secret for outbound webhook signing. Retrievable via `/me`. |
| `approved_by` | FK → User | Superadmin who last approved. |
| `decided_at` | DateTimeField | When the latest approve/reject/suspend decision was taken. |
| `created_datetime` | DateTimeField | Auto-set on creation. |
| `updated_datetime` | DateTimeField | Auto-updated on every save. |

**DB constraints** (enforced at the database level):

```sql
-- api_key_hash must be empty unless status is APPROVED or SUSPENDED
CHECK (api_key_hash = '' OR status IN ('APPROVED', 'SUSPENDED'))
  name: mie_dev_key_requires_active_status

-- signing_secret must be empty unless status is APPROVED or SUSPENDED
CHECK (signing_secret = '' OR status IN ('APPROVED', 'SUSPENDED'))
  name: mie_dev_secret_requires_active_status
```

These constraints guarantee that rejected and pending accounts never have
credential material on the row, regardless of application-level bugs.

### 3.2 CourseSubmission

**File**: `api/mie/models/course_submission.py`

A course idea submitted by an external developer.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto-generated. |
| `developer` | FK → DeveloperAccount | Cascading delete. `related_name="submissions"`. |
| `payload` | JSONField | The submission body **verbatim** — never rewritten. |
| `title` | CharField(255) | Extracted from payload. Key for all three dedup checks. |
| `status` | CharField(25) | See `SubmissionStatus` enum below. |
| `rejection_reason` | FK → SubmissionRejectionReason | Nullable. SET_NULL on delete. |
| `rejection_note` | TextField | Free-text detail accompanying rejection. |
| `demand_score` | PositiveSmallIntegerField | Admin-entered 0–100 market-demand signal. |
| `estimated_monthly_earnings` | DecimalField(12,2) | Admin-entered earnings estimate. |
| `queued_at` | DateTimeField | When the idea most recently entered the review queue. |
| `decided_at` | DateTimeField | When the latest approve/reject decision was taken. |
| `decided_by` | FK → User | Superadmin responsible for the latest decision. |
| `payout_bypass` | BooleanField | Per-submission no-payout marker. |
| `resulting_course` | OneToOne → Course | Nullable. SET_NULL on delete. `related_name="mie_submission"`. |

**DB constraints:**

```sql
-- Only one PENDING_REVIEW row per title (case-insensitive)
UNIQUE (LOWER(title)) WHERE status = 'PENDING_REVIEW'
  name: unique_pending_title_in_queue

-- APPROVED or REJECTED rows must have decided_at set
CHECK (NOT (status IN ('APPROVED','REJECTED')) OR decided_at IS NOT NULL)
  name: mie_decided_submission_has_decision
```

**DB indexes:**

| Index name | Fields | Purpose |
|---|---|---|
| `mie_sub_status_idx` | `status`, `-created_datetime` | Admin queue ordering + filter |
| `mie_sub_dev_status_idx` | `developer`, `status` | Developer queue scoping |

### 3.3 SubmissionRejectionReason

**File**: `api/mie/models/rejection_reason.py`

Admin-managed taxonomy backing dedup check #1. Labels are unique and
soft-deactivated (`is_active=False`) rather than deleted, so historical
submissions keep pointing at them.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto-generated. |
| `label` | CharField(255) | **Unique.** Matched against past rejections. |
| `description` | TextField | Longer explanation of when this reason applies. |
| `is_active` | BooleanField | Inactive reasons stop matching new ideas. |
| `created_datetime` | DateTimeField | Auto-set on creation. |

### 3.4 WebhookEvent

**File**: `api/mie/models/webhook_event.py`

One outbound webhook notification to a developer. Created immediately on
every submission transition — including automated dedup short-circuits.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Developer-facing dedup key (`event_id`). |
| `submission` | FK → CourseSubmission | Cascading delete. `related_name="webhook_events"`. |
| `event_type` | CharField(40) | See `WebhookEventType` enum. |
| `payload` | JSONField | Exact JSON body delivered (or to be delivered). |
| `signature` | CharField(128) | HMAC-SHA256 hex digest (computed at send time, not at record time). |
| `delivery_status` | CharField(10) | `PENDING` / `DELIVERED` / `FAILED` |
| `attempts` | PositiveSmallIntegerField | Delivery attempts so far. |
| `last_response_code` | PositiveSmallIntegerField | HTTP status from the developer's endpoint. |
| `last_error` | CharField(500) | Transport or timeout error from the latest attempt. |
| `next_retry_at` | DateTimeField | Earliest moment the dispatcher may try again. |
| `delivered_at` | DateTimeField | When the event was successfully delivered. |

**DB indexes:**

| Index name | Fields | Purpose |
|---|---|---|
| `mie_hook_retry_idx` | `delivery_status`, `next_retry_at` | Dispatcher sweep query |
| `mie_hook_sub_idx` | `submission`, `-created_datetime` | Per-submission event history |

---

## 4. Authentication

MIE uses a **dual-path authentication** system. Every developer-facing
route accepts either credential kind — the resolved `DeveloperAccount` is
always stored as `request.auth`.

**File**: `api/mie/authentication.py`

### 4.1 API Key path

```
Header: X-MIE-Api-Key: scb_live_...
```

1. Key must start with `scb_live_` prefix.
2. First 16 characters (`api_key_prefix`) are used for an indexed DB
   lookup — at most one row is ever examined.
3. Full key is SHA-256 hashed and compared against `api_key_hash` using
   `hmac.compare_digest` (constant-time).
4. Account status is checked: `APPROVED` passes, `SUSPENDED` returns a
   distinct error code, everything else fails.
5. `api_key_last_used_at` is updated on every successful auth.

**Key material lifecycle:**

- Generated by `key_service.issue_credentials()` — returns the raw key
  exactly once (at approval or rotation).
- Only `api_key_prefix` (non-secret) and `api_key_hash` (SHA-256 hex)
  are stored. The raw key is never persisted.
- Revoked by `key_service.revoke_key()` — clears prefix, hash, and
  signing secret. DB constraints enforce the clear.

### 4.2 Platform JWT path

```
Header: Authorization: Bearer <token>
```

1. Token is a PyJWT signed with `settings.SIMPLE_JWT["SIGNING_KEY"]`.
2. Must contain claims: `mie_developer_id`, `typ` (= `"mie_dev"`), `iat`,
   `exp`.
3. `mie_developer_id` is resolved to a `DeveloperAccount` via exact UUID
   lookup.
4. Status is re-checked on every request — suspension kills live sessions
   immediately.
5. Lifetime mirrors `SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"]`.

External developers have **no rows in the platform user table**. The JWT
carries `mie_developer_id` and is worthless outside MIE endpoints.

### 4.3 Permission gate

**File**: `api/mie/permissions.py`

`IsMieDeveloper` is the second half of the auth pair. It checks:

```python
isinstance(request.auth, DeveloperAccount)
and request.auth.status == DeveloperAccountStatus.APPROVED
```

Fails closed: if any future auth path forgets the status check, this
permission still blocks non-active accounts.

### 4.4 OpenAPI security scheme

`MieDeveloperAuthentication` is registered with drf-spectacular as
`mieDeveloperAuth` — a lazily-initialized `OpenApiAuthenticationExtension`
that describes the dual-key scheme in the generated OpenAPI spec.

---

## 5. Developer Lifecycle

### 5.1 Self-registration

**Endpoint**: `POST /api/v1/mie/v1/register/` (public, rate-limited)
**View**: `MieDeveloperRegistrationView`
**Service**: `developer_service.register_developer()`

The registration endpoint is public (`AllowAny`) with a scoped throttle
(`mie_register`, 5/hour). It creates a `DeveloperAccount` in `PENDING`
status with only email and webhook_url populated. No API key exists; the
account can authenticate nothing.

Duplicate email check is case-insensitive (`email__iexact`).

### 5.2 Admin approval

**Endpoint**: `POST /api/v1/mie/admin/developers/{id}/approve/`
**Service**: `developer_service.approve_developer()`

Approval is the moment credentials are issued:

1. Status flips to `APPROVED` **before** credential issuance (DB constraint
   requires active status to hold key material).
2. If no key exists (first approval, or re-approval after rejection
   wiped credentials), `key_service.issue_credentials()` generates a
   fresh key pair.
3. The raw API key is returned in the response as `one_time_api_key` —
   shown exactly once, never retrievable again.
4. If the account already has valid credentials, `one_time_api_key` is
   `None` and the existing key is retained.

Approval is reversible — REJECTED and SUSPENDED accounts can be approved
again. Already-approved accounts return 400.

### 5.3 Rejection

**Endpoint**: `POST /api/v1/mie/admin/developers/{id}/reject/`
**Service**: `developer_service.reject_developer()`

Rejects from any state and:

1. Revokes all credential material (API key prefix, hash, signing secret).
2. Sets status to `REJECTED`.
3. Calls `webhook_dispatcher.drop_events_for_rejected_account()` to
   terminal-fail all pending webhook events for this developer.

Rejection is reversible — re-approval issues fresh credentials.

### 5.4 Suspension

**Endpoint**: `POST /api/v1/mie/admin/developers/{id}/suspend/`
**Service**: `developer_service.suspend_developer()`

Freezes an APPROVED account:

1. Status flips to `SUSPENDED`.
2. API key and signing secret remain on the row (DB constraint allows it)
   but the auth layer rejects them.
3. Queue history, webhook configuration, and all data are retained.
4. Pending webhook events stay `PENDING` — they will be delivered if the
   account returns to APPROVED.
5. Only APPROVED accounts can be suspended; attempting to suspend a
   PENDING, REJECTED, or SUSPENDED account returns 400.

---

## 6. Ingest + Dedup Engine (Endpoint 1)

**Endpoint**: `POST /api/v1/mie/v1/submissions/`
**View**: `MieSubmissionIngestView`
**Service**: `submission_service.submit_idea()`

### 6.1 Request format

```json
{
  "title": "Build a Production-Grade Rust Course",
  "description": "optional extra context",
  "audience": "mid-level backend developers"
}
```

Only `title` is required. The body is stored **verbatim** in the
`payload` JSONField — no fields are extracted or rewritten beyond the
title. This means the admin review surfaces see exactly what the
developer submitted.

### 6.2 Title validation

`submission_service.validate_idea_payload()`:

- Body must be a JSON object.
- `title` must be a non-empty string, ≤ 255 characters after trimming.
- Trimmed form is stored and used for all dedup comparisons.

### 6.3 Dedup engine

**File**: `api/mie/services/dedup_service.py`

Three sequential checks, first match wins:

| # | Check | Status set | Webhook event | Notes |
|---|---|---|---|---|
| 1 | Title matches a previously REJECTED submission | `PREVIOUSLY_REJECTED` | `SUBMISSION_PREVIOUSLY_REJECTED` | Inherits the rejection reason from the prior rejection. |
| 2 | Title matches an existing platform Course | `DUPLICATE_EXISTING` | `SUBMISSION_DUPLICATE_EXISTING` | Checks `Course.objects.filter(title__iexact=...)`. |
| 3 | Title already PENDING_REVIEW in queue | `DUPLICATE_IN_QUEUE` | `SUBMISSION_DUPLICATE_IN_QUEUE` | Enforced by partial unique index. |
| 4 | No match | `PENDING_REVIEW` | `SUBMISSION_QUEUED` | Normal path — idea enters the review queue. |

Titles are compared case-insensitively after trimming.

### 6.4 Race condition handling

The partial unique index on `(LOWER(title)) WHERE status = PENDING_REVIEW`
can fire an `IntegrityError` if two developers submit the same title
simultaneously. When this happens:

1. The transaction catches `IntegrityError`.
2. `_record_lost_race()` creates the submission as `DUPLICATE_IN_QUEUE`
   without re-running dedup (re-running could loop under repeated
   contention).
3. A `SUBMISSION_DUPLICATE_IN_QUEUE` webhook event is recorded.

This guarantees that lost races resolve deterministically as duplicates.

### 6.5 Webhook event recording

Every outcome — including dedup short-circuits — immediately records a
`WebhookEvent` row via `submission_service.record_event()`. The row exists
from the moment the transition happens; nothing can be lost between
ingestion and delivery.

### 6.6 Throttling

Scoped throttle `mie_ingest`: 30 requests per minute per API key.

### 6.7 Response

```json
{
  "id": "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11",
  "reference": "SCB-0d1c7b2e-P",
  "status": "PENDING_REVIEW",
  "created_datetime": "2026-08-23T09:00:00Z"
}
```

HTTP 201 regardless of dedup outcome — the submission was accepted and
processed; the `status` field indicates which outcome applied.

---

## 7. Webhook Dispatcher

**File**: `api/mie/services/webhook_dispatcher.py`

### 7.1 Design contract: Record-then-sweep

Transition code (in `submission_service` and `submission_admin_service`)
only writes `WebhookEvent` rows. The dispatcher is the **sole sender**.
This separation means:

- No network calls inside transaction blocks.
- Events survive process crashes.
- Overlapping sweep passes are safe.

### 7.2 Dispatch pass

`dispatch_due_events()` runs one delivery pass:

1. **Query due events** — one indexed query using `mie_hook_retry_idx`
   (`delivery_status=PENDING` AND (`next_retry_at IS NULL` OR
   `next_retry_at <= now`)), with `select_related("submission",
   "submission__developer")` — no N+1.

2. **Partition by account state** — deliverable (APPROVED), inactive
   (REJECTED → terminal-fail immediately), suspended (frozen — left
   PENDING for later).

3. **Prepare requests** — for each deliverable event, compute the
   signing headers and body. No DB access inside threads.

4. **Concurrent POSTs** — bounded thread pool (8 workers, HTTP only).
   Uses `httpx.post()` with connect timeout 5s, read timeout 10s.

5. **Apply outcomes** — single pass of `bulk_update` (batch size 200)
   for all events in one write.

### 7.3 Retry backoff

| Attempt | Delay before next |
|---|---|
| 1 → 2 | 60 seconds |
| 2 → 3 | 5 minutes |
| 3 → 4 | 15 minutes |
| 4 → 5 | 1 hour |
| 5+ | Terminal `FAILED` |

Constants: `MAX_ATTEMPTS = 5`, `RETRY_DELAYS_SECONDS = (60, 300, 900, 3600)`.

### 7.4 Signing scheme

```
X-MIE-Timestamp: <unix seconds at send time>
X-MIE-Signature: hex(HMAC-SHA256("{timestamp}.{raw_body}", signing_secret))
```

The raw body is canonical JSON (sorted keys, compact separators):

```json
{
  "event_id": "<uuid>",
  "type": "SUBMISSION_QUEUED",
  "occurred_at": "2026-08-23T09:00:00+00:00",
  "submission": {
    "reference": "SCB-0d1c7b2e-P",
    "status": "PENDING_REVIEW",
    "title": "Build a Production-Grade Rust Course"
  }
}
```

The `signing_secret` is the developer's per-account HMAC key, retrievable
via `GET /api/v1/mie/v1/me/`. Receivers should recompute the signature
and reject events with timestamps older than `REPLAY_WINDOW_SECONDS = 300`.

### 7.5 Account state effects

| Account status | Dispatcher behavior |
|---|---|
| `APPROVED` | Events delivered normally. |
| `SUSPENDED` | Events left PENDING — frozen, not failed. Delivered on revival. |
| `REJECTED` | Events terminal-failed immediately. `drop_events_for_rejected_account()` is also called at rejection time. |

### 7.6 Empty queue cost

One `SELECT` query (~40ms), returns immediately.

### 7.7 Scheduling

Two equivalent entry points:

1. **Celery beat** — `tasks.dispatch_due_webhooks_task()`, scheduled every
   60 seconds.
2. **Management command** — `python manage.py dispatch_mie_webhooks`.

---

## 8. Developer Surfaces

All developer-facing routes use `MieDeveloperAuthentication` +
`IsMieDeveloper`. Results are hard-scoped server-side to the authenticated
developer — no query parameter can expose another developer's data.

### 8.1 Submit a course idea

```
POST /api/v1/mie/v1/submissions/
```

Endpoint 1 — the primary ingestion surface. See [Section 6](#6-ingest--dedup-engine-endpoint-1).

### 8.2 Submission queue

```
GET /api/v1/mie/v1/submissions/queue/
```

Returns the authenticated developer's complete submission queue — every
idea in every pipeline state, ordered newest first.

**Filters:**

| Parameter | Type | Description |
|---|---|---|
| `?status=` | string | Restrict to one pipeline state. |
| `?search=` | string | Case-insensitive substring match on title. |

Queryset: `CourseSubmission.objects.filter(developer=self.request.auth)`.

### 8.3 Account snapshot

```
GET /api/v1/mie/v1/me/
```

Returns the developer's account details:

```json
{
  "email": "dev@studio.io",
  "status": "APPROVED",
  "plan_type": "PAID_PER_SUBMISSION",
  "webhook_url": "https://hooks.studio.io/mie",
  "api_key_preview": "scb_live_a1b2c3d4...",
  "api_key_last_used_at": "2026-07-15T08:32:11Z",
  "signing_secret": "a1b2c3d4e5f6...",
  "created_datetime": "2026-06-28T14:22:00Z",
  "decided_at": "2026-07-01T10:00:00Z"
}
```

- `api_key_preview` is always masked (`{prefix}...`) — the full key is
  never returned after initial issuance.
- `signing_secret` IS included — it only verifies our messages, never
  authenticates theirs.

### 8.4 Integration documentation

```
GET /api/v1/mie/v1/documentation/
```

Returns a machine-readable documentation payload generated from live code
constants:

```json
{
  "plan": {
    "plan_type": "PAID_PER_SUBMISSION",
    "explanation": "Each approved idea credits the creator wallet at approval time."
  },
  "authentication": {
    "api_key_header": "X-MIE-Api-Key",
    "example": "scb_live_a1b2c3d4...",
    "note": "Send your full key in the X-MIE-Api-Key header..."
  },
  "reference_scheme": {
    "description": "Every submission has an immutable id and a public reference...",
    "suffixes": [
      {"status": "PENDING_REVIEW", "suffix": "P", "meaning": "queued for admin review"},
      {"status": "DUPLICATE_IN_QUEUE", "suffix": "D", "meaning": "same title already awaiting review"},
      {"status": "DUPLICATE_EXISTING", "suffix": "E", "meaning": "a platform course already uses this title"},
      {"status": "PREVIOUSLY_REJECTED", "suffix": "X", "meaning": "this exact title was rejected before"},
      {"status": "APPROVED", "suffix": "A", "meaning": "accepted; a course is being produced"},
      {"status": "REJECTED", "suffix": "R", "meaning": "rejected by an admin"}
    ]
  },
  "endpoints": [...],
  "webhooks": {
    "delivery": "A POST is sent to your webhook_url immediately for every event...",
    "signature_verification": {
      "headers": ["X-MIE-Signature", "X-MIE-Timestamp"],
      "scheme": "hex-encoded HMAC-SHA256 over '{timestamp}.{raw request body}'..."
    },
    "samples": { ... }
  }
}
```

Every value is derived from live code — enum choices, route table,
event-payload builder — so documentation can never drift from the API.

---

## 9. Admin Console

All admin endpoints require the `IsSuperAdminRole` permission.

### 9.1 Developer directory

**Viewset**: `MieDeveloperAdminViewSet`

```
GET    /api/v1/mie/admin/developers/          # List (paginated)
GET    /api/v1/mie/admin/developers/{id}/      # Retrieve
POST   /api/v1/mie/admin/developers/           # Register manually (PENDING)
POST   /api/v1/mie/admin/developers/{id}/approve/   # Approve
POST   /api/v1/mie/admin/developers/{id}/reject/    # Reject
POST   /api/v1/mie/admin/developers/{id}/suspend/   # Suspend
```

**List filters**: `?status=`, `?plan_type=`, `?search=` (email substring).

The approve response includes `one_time_api_key` (the full raw key,
shown exactly once) or `null` if existing credentials are retained.

### 9.2 Cross-developer submission queue

**Viewset**: `MieSubmissionAdminViewSet`

```
GET    /api/v1/mie/admin/submissions/          # List (paginated)
GET    /api/v1/mie/admin/submissions/{id}/      # Retrieve
POST   /api/v1/mie/admin/submissions/{id}/approve/     # Approve
POST   /api/v1/mie/admin/submissions/{id}/reject/      # Reject
POST   /api/v1/mie/admin/submissions/{id}/signals/     # Set demand signals
POST   /api/v1/mie/admin/submissions/{id}/payout_bypass/  # Toggle bypass
```

**Filters** (via `AdminSubmissionFilterSet`):

| Parameter | Type | Description |
|---|---|---|
| `?developer=` | UUID | Filter to one developer account id. |
| `?email=` | string | Filter to one developer by exact email. |
| `?status=` | string | One pipeline state. |
| `?payout_bypass=` | bool | Filter to bypassed or paying ideas. |
| `?created_after=` | ISO-8601 | Lower bound on arrival time. |
| `?created_before=` | ISO-8601 | Upper bound on arrival time. |
| `?search=` | string | Case-insensitive substring on title OR developer email. |

### 9.3 Reversible approve/reject

Decisions are **not terminal** — a superadmin can flip `APPROVED` ↔
`REJECTED` at any time. Every flip:

1. Updates the submission status, `decided_at`, and `decided_by`.
2. Records a new `WebhookEvent` (`SUBMISSION_APPROVED` or
   `SUBMISSION_REJECTED`).
3. If rejecting from APPROVED with a `resulting_course`, flags the course
   out of production (`NEEDS_REVISION`) — never deletes it.
4. If re-approving from REJECTED, clears stale rejection metadata.

### 9.4 Recommendation signals

**Endpoint**: `POST /api/v1/mie/admin/submissions/{id}/signals/`

Sets advisory metadata — no webhook is fired:

- `demand_score`: integer 0–100.
- `estimated_monthly_earnings`: optional decimal, nullable.

### 9.5 Payout bypass toggle

**Endpoint**: `POST /api/v1/mie/admin/submissions/{id}/payout_bypass/`

Fires a `SUBMISSION_PAYOUT_BYPASS_UPDATED` webhook on every change.
Rejects silently (returns current submission without change) if the
bypass is already in the requested state.

### 9.6 Rejection-reason taxonomy CRUD

**Viewset**: `RejectionReasonAdminViewSet`

```
GET    /api/v1/mie/admin/rejection-reasons/          # List
POST   /api/v1/mie/admin/rejection-reasons/           # Create
GET    /api/v1/mie/admin/rejection-reasons/{id}/      # Retrieve
PATCH  /api/v1/mie/admin/rejection-reasons/{id}/      # Partial update
```

Labels are unique. Reasons are soft-deactivated (`is_active=false`) —
never hard-deleted, so historical submissions retain their references.
No DELETE endpoint is exposed (HTTP methods restricted to GET, POST,
PATCH, HEAD, OPTIONS).

---

## 10. Reference Scheme

**File**: `api/mie/services/reference.py`

Public references follow the format `SCB-{id}-{suffix}` where:

- `{id}` is the first 8 characters of the UUID (hyphens stripped).
- `{suffix}` is a single letter reflecting the **live** status.

The suffix is computed dynamically from `submission.status` via the
`public_reference` property — it always reflects reality:

| Status | Suffix | Meaning |
|---|---|---|
| `PENDING_REVIEW` | **P** | Queued for admin review |
| `DUPLICATE_IN_QUEUE` | **D** | Same title already awaiting review |
| `DUPLICATE_EXISTING` | **E** | A platform course already uses this title |
| `PREVIOUSLY_REJECTED` | **X** | This exact title was rejected before |
| `APPROVED` | **A** | Accepted; a course is being produced |
| `REJECTED` | **R** | Rejected by an admin |

Example: `SCB-0d1c7b2e-P` (pending), `SCB-0d1c7b2e-A` (approved).

The reference letter changes as the idea moves — developers should use
the reference as their correlation key in webhook payloads and their own
systems.

---

## 11. Event Types

**Enum**: `WebhookEventType` in `api/mie/enums.py`

Every event type maps 1:1 onto a `SubmissionStatus` transition (plus the
payout-bypass update). Events are fired immediately on every transition,
including automated dedup short-circuits.

| Event Type | When fired | Webhook payload extras |
|---|---|---|
| `SUBMISSION_QUEUED` | New idea passes all dedup checks → `PENDING_REVIEW` | — |
| `SUBMISSION_DUPLICATE_IN_QUEUE` | Title already in queue → `DUPLICATE_IN_QUEUE` | — |
| `SUBMISSION_DUPLICATE_EXISTING` | Title matches existing course → `DUPLICATE_EXISTING` | — |
| `SUBMISSION_PREVIOUSLY_REJECTED` | Title was rejected before → `PREVIOUSLY_REJECTED` | — |
| `SUBMISSION_APPROVED` | Admin approves (or re-approves) | — |
| `SUBMISSION_REJECTED` | Admin rejects (or re-rejects) | `rejection_reason`, `rejection_note` |
| `SUBMISSION_PAYOUT_BYPASS_UPDATED` | Admin toggles per-submission bypass | `payout_bypass` |

The mapping from status to event type is defined in
`submission_service.EVENT_TYPE_BY_STATUS` for ingestion events, and
explicitly in `submission_admin_service.decide_submission()` and
`set_payout_bypass()` for admin decisions.

---

## 12. Error Handling

### 12.1 Dedup short-circuits

Dedup outcomes return immediately with the appropriate status — there is
no error to handle. The developer sees a 201 with the dedup status in the
response body. The webhook event is recorded for every outcome.

### 12.2 Race conditions

Lost races against the partial unique index resolve deterministically as
`DUPLICATE_IN_QUEUE` (see [Section 6.4](#64-race-condition-handling)).
No retry logic is needed on the client side.

### 12.3 Rate limiting

| Endpoint | Throttle scope | Limit |
|---|---|---|
| `POST /api/v1/mie/v1/submissions/` | `mie_ingest` | 30/min/key |
| `POST /api/v1/mie/v1/register/` | `mie_register` | 5/hour/IP |

### 12.4 Webhook delivery retries

Failed deliveries are retried with exponential backoff (60s → 5m → 15m →
1h) up to 5 attempts, then terminal `FAILED`. The `next_retry_at` field
controls when the next attempt may occur.

### 12.5 DB constraints as safety net

The database enforces invariants that the application layer also
maintains:

- Credential material (`api_key_hash`, `signing_secret`) can only exist
  on `APPROVED` or `SUSPENDED` rows.
- Only one `PENDING_REVIEW` submission per title (case-insensitive).
- `APPROVED` or `REJECTED` submissions must have `decided_at` set.

These constraints prevent credential leakage and data corruption even if
application code has bugs.

### 12.6 Validation errors

Standard DRF 400 responses for:

- Missing or invalid `title` in submission payload.
- Duplicate email at registration.
- Rejecting without a `rejection_reason`.
- Approving an already-approved account.
- Suspending a non-APPROVED account.
- Setting `demand_score` outside 0–100 range.
- Toggling payout bypass to the same state it's already in.

---

## 13. File Map

### Models (`api/mie/models/`)

| File | Contents |
|---|---|
| `__init__.py` | Re-exports all 4 models |
| `developer_account.py` | `DeveloperAccount` — external developer identity + credentials |
| `course_submission.py` | `CourseSubmission` — submitted course idea with dedup status |
| `rejection_reason.py` | `SubmissionRejectionReason` — admin-managed rejection taxonomy |
| `webhook_event.py` | `WebhookEvent` — outbound delivery record with retry state |

### Services (`api/mie/services/`)

| File | Contents |
|---|---|
| `__init__.py` | Empty |
| `key_service.py` | API key generation, hashing, revocation, and authentication |
| `dev_token_service.py` | PyJWT session token issuance and resolution |
| `developer_service.py` | Registration, approval, rejection, suspension logic |
| `submission_service.py` | Ingestion, payload validation, event recording, race handling |
| `submission_admin_service.py` | Approve/reject, demand signals, payout bypass |
| `dedup_service.py` | Three-stage title dedup engine |
| `webhook_dispatcher.py` | Record-then-sweep delivery, signing, retry, partitioning |
| `documentation_service.py` | Machine-readable docs from live constants |
| `reference.py` | `REFERENCE_SUFFIXES` mapping status → suffix letter |

### Views (`api/mie/views/`)

| File | Contents |
|---|---|
| `__init__.py` | Re-exports all view classes |
| `dev_registration_views.py` | `MieDeveloperRegistrationView` — public self-registration |
| `dev_submission_views.py` | `MieSubmissionIngestView` (POST), `MieSubmissionQueueView` (GET) |
| `dev_account_views.py` | `MieDeveloperMeView`, `MieDocumentationView` |
| `admin_developer_views.py` | `MieDeveloperAdminViewSet` — list, retrieve, create, approve, reject, suspend |
| `admin_submission_views.py` | `MieSubmissionAdminViewSet` — list, retrieve, approve, reject, signals, payout_bypass |
| `rejection_reason_views.py` | `RejectionReasonAdminViewSet` — CRUD for rejection taxonomy |

### Serializers (`api/mie/serializers/`)

| File | Contents |
|---|---|
| `submission_serializer.py` | `SubmissionIngestSerializer`, `SubmissionIngestResponseSerializer` |
| `dev_submission_serializer.py` | `DevSubmissionSerializer` — developer queue row |
| `admin_submission_serializer.py` | `AdminSubmissionSerializer`, `SubmissionDecisionSerializer`, `DemandSignalsSerializer`, `PayoutBypassSerializer` |
| `developer_admin_serializer.py` | `DeveloperRegisterSerializer`, `DeveloperAccountAdminSerializer`, `DeveloperApprovalResponseSerializer`, `DeveloperActionResponseSerializer` |
| `dev_me_serializer.py` | `DeveloperMeSerializer` — account snapshot with masked key |
| `rejection_reason_serializer.py` | `RejectionReasonSerializer` |

### Other files

| File | Contents |
|---|---|
| `authentication.py` | `MieDeveloperAuthentication` — dual-path DRF auth class + drf-spectacular extension |
| `permissions.py` | `IsMieDeveloper` — APPROVED status gate |
| `filters.py` | `AdminSubmissionFilterSet` — django-filters for admin queue |
| `enums.py` | `DeveloperAccountStatus`, `MiePlanType`, `SubmissionStatus`, `WebhookEventType`, `WebhookDeliveryStatus` |
| `urls.py` | Route registration: DefaultRouter for admin viewsets + explicit paths for developer endpoints |
| `admin.py` | Django admin registrations for all 4 models |
| `apps.py` | `MieConfig` AppConfig |
| `tasks.py` | `dispatch_due_webhooks_task` — Celery shared_task wrapper |
| `management/commands/dispatch_mie_webhooks.py` | `python manage.py dispatch_mie_webhooks` |
| `migrations/0001_initial.py` | All 4 tables + constraints + indexes |
| `migrations/0002_alter_webhookevent_event_type.py` | Adds `SUBMISSION_PAYOUT_BYPASS_UPDATED` to event_type choices |
