# MIE — Frontend Integration Guide

Everything the FE needs to build the external-developer pipeline UI: the story, the journey, every endpoint, every enum, and the webhook contract. No tribal knowledge required.

---

## The story in one paragraph

External developers ("partners") send us **course ideas**. A developer registers with an email + webhook URL, waits for a superadmin to approve them (at which point they receive an API key **shown exactly once**), then submits ideas over the API. Each idea is deduplicated instantly against three things — previously rejected titles, existing course titles, and ideas already awaiting review — and lands in a queue. Superadmins review, score, and approve/reject ideas; **every state change fires a signed webhook** to the developer. Decisions are reversible at any time, and the developer's view always reflects live state.

---

## The journey at a glance

```
STEP 1  Developer self-registers          POST /mie/v1/register/          (public)
        └─ account = PENDING, can do nothing yet
STEP 2  Superadmin approves                POST /mie/admin/developers/{id}/approve/
        └─ response contains the API key ONCE → developer copies it
STEP 3  Developer submits + tracks ideas   POST/GET /mie/v1/…              (API key)
STEP 4  Developer reads account + docs     GET /mie/v1/me/, /documentation/
STEP 5  Superadmin works the queue         /mie/admin/submissions/…        (admin JWT)
```

Swagger groups appear in exactly this order (tags: `MIE Developer — Onboarding`, `Admin — MIE Developers`, `MIE Developer — Submissions`, `MIE Developer — Account`, `Admin — MIE Submissions`).

---

## Authentication — two paths into the same developer routes

| Path | Header | Where it comes from |
|---|---|---|
| **API key** | `X-MIE-Api-Key: scb_live_<43 chars>` | Issued by the superadmin's approve action; shown **exactly once** in that response. Never retrievable again (only rotate). |
| **Platform session** | `Authorization: Bearer <token>` | The FE's normal OTP login flow for the developer's email. Same endpoints, same data. |

- Developer-facing routes accept **either** — build one view, both work.
- Admin routes (`/mie/admin/…`) accept **only** the superadmin's platform JWT (`Authorization: Bearer …` from `/auth/login/`). API keys are rejected there by design.
- A suspended/rejected account fails everything with `401` + a machine-readable code (`account_suspended`, `account_not_active`).

---

## Endpoints, step by step

### STEP 1 — Developer registers (public)

| | |
|---|---|
| `POST /api/v1/mie/v1/register/` | Body: `{ "email", "webhook_url" }` → `201` with the account (`status: PENDING`, `api_key_preview: null`). Rate-limited 5/hour/IP. Duplicate email → `400`. |

FE renders: a registration form; success screen = "pending approval".

### STEP 2 — Superadmin approves

**Approval is the delivery moment:** the approve response contains the API key, and from that instant the developer's full integration documentation (`GET /mie/v1/documentation/`) is live. The docs never expire — they stay reachable anytime via the developer's profile (`/me`) or the documentation endpoint.

| | |
|---|---|
| `GET /api/v1/mie/admin/developers/?status=PENDING` | Directory. Filters: `?status=`, `?plan_type=`, `?search=` (email). |
| `POST /api/v1/mie/admin/developers/{id}/approve/` | **The response body contains `one_time_api_key`** — render it once with a copy button, right next to a "view documentation" link (`GET /mie/v1/documentation/`). After this response the key does not exist anywhere retrievable. On re-activating a suspended account it is `null` (old key still valid). |
| `POST …/reject/` | Revokes credentials immediately. Reversible. |
| `POST …/suspend/` | Freezes an APPROVED account (keys stop working; data retained). Reversible via approve. |

### STEP 3 — Developer submits & tracks (API key or platform session)

| | |
|---|---|
| `POST /api/v1/mie/v1/submissions/` | **Endpoint 1.** Body: `title` (required, ≤255 chars) + any extra keys (stored verbatim). → `201 { id, reference, status }`. The dedup engine decides `status` instantly. Throttled 30/min/key. |
| `GET /api/v1/mie/v1/submissions/queue/` | **The developer's own queue — hard-scoped server-side.** Every state appears, newest first. Filters: `?status=`, `?search=` (title substring). |

Dedup outcomes at submission time:

| Returned status | Meaning | Ref suffix |
|---|---|---|
| `PENDING_REVIEW` | queued for admin review | `-P` |
| `DUPLICATE_IN_QUEUE` | same title already awaiting review | `-D` |
| `DUPLICATE_EXISTING` | a platform course already has this title | `-E` |
| `PREVIOUSLY_REJECTED` | this exact title was rejected before (inherits the old reason) | `-X` |

### STEP 4 — Developer reference material (available anytime after approval)

| | |
|---|---|
| `GET /api/v1/mie/v1/me/` | Account snapshot: `status`, `plan_type`, `webhook_url`, `api_key_preview` (masked, e.g. `scb_live_Ab3dEf...`), `signing_secret` (for verifying our webhooks — safe to display), `api_key_last_used_at`. Doubles as a credentials health-check. This is the developer's **profile page** — the permanent home for their key preview, secret, and a link to the documentation. |
| `GET /api/v1/mie/v1/documentation/` | Machine-generated integration doc: their plan semantics, endpoint list, suffix table, webhook samples + HMAC recipe. Render it or link it. Surfaced alongside the API key at approval and **always re-accessible here** — a developer who loses their onboarding email can always come back to this. |

### STEP 5 — Superadmin works the pipeline (admin JWT only)

| | |
|---|---|
| `GET /api/v1/mie/admin/submissions/` | **Cross-developer queue.** Filters: `?developer=<uuid>`, `?email=`, `?status=`, `?payout_bypass=`, `?created_after=`, `?created_before=`, `?search=` (title or dev email). Includes the verbatim Endpoint 1 payload, demand signals, decision metadata. |
| `POST …/{id}/approve/` | Accepts the idea. Works from **any** state (reversible). Fires `SUBMISSION_APPROVED` immediately. Body: `{}`. |
| `POST …/{id}/reject/` | Body: `{ "rejection_reason": "<label>", "rejection_note": "…" }` — reason label is **required** (get labels from the taxonomy endpoint below). Works from any state incl. APPROVED. Fires `SUBMISSION_REJECTED`. |
| `POST …/{id}/signals/` | `{ "demand_score": 0–100, "estimated_monthly_earnings": "4200.00" }` — the Recommendations-queue prioritisation inputs. Fires no webhook. |
| `POST …/{id}/payout_bypass/` | `{ "payout_bypass": true|false }` — marks this one idea no-payout. Fires `SUBMISSION_PAYOUT_BYPASS_UPDATED` each way. Identical toggles → `400`. |
| `GET/POST/PATCH /api/v1/mie/admin/rejection-reasons/` | The reason taxonomy. `?is_active=` filter. No delete — soft-deactivate via `"is_active": false`. |

**Reversal behavior the FE should know:** flipping APPROVED → REJECTED after a course was produced unpublishes that course (never deletes) and keeps the link; re-approving relinks it. Re-approving a rejected idea clears its rejection metadata.

---

## Enums (authoritative values)

### SubmissionStatus — `status` on every submission
`PENDING_REVIEW` · `DUPLICATE_IN_QUEUE` · `DUPLICATE_EXISTING` · `PREVIOUSLY_REJECTED` · `APPROVED` · `REJECTED`

### DeveloperAccountStatus — account lifecycle
`PENDING` (cannot authenticate) · `APPROVED` (full access) · `REJECTED` (terminal; re-approve issues fresh key) · `SUSPENDED` (frozen, reversible)

### MiePlanType — payout arrangement (superadmin-set, shown in /me + docs)
`PAID_PER_SUBMISSION` (each approval pays) · `BYPASS_PER_SUBMISSION` (pays unless that idea is bypassed) · `BYPASS_ACCOUNT` (never pays)

### WebhookEventType — what lands on the developer's webhook
`SUBMISSION_QUEUED` · `SUBMISSION_DUPLICATE_IN_QUEUE` · `SUBMISSION_DUPLICATE_EXISTING` · `SUBMISSION_PREVIOUSLY_REJECTED` · `SUBMISSION_APPROVED` · `SUBMISSION_REJECTED` · `SUBMISSION_PAYOUT_BYPASS_UPDATED`

### WebhookDeliveryStatus — internal delivery bookkeeping (admin queue only)
`PENDING` · `DELIVERED` · `FAILED`

---

## The public reference (`reference` field)

Format: **`SCB-xxxxxxxx-S`** — 8 chars of the immutable id + a suffix letter that **always mirrors current status**:

| Suffix | Status |
|---|---|
| `P` | PENDING_REVIEW |
| `D` | DUPLICATE_IN_QUEUE |
| `E` | DUPLICATE_EXISTING |
| `X` | PREVIOUSLY_REJECTED |
| `A` | APPROVED |
| `R` | REJECTED |

Use `reference` as the correlation key everywhere (queue rows, webhook payloads). The `id` never changes; the suffix does — that's the point.

---

## Webhooks (what the developer's endpoint receives)

- A `POST` is sent for **every** transition — including automated dedup outcomes and payout-bypass changes. No polling exists; the webhook is the only notification channel.
- Respond `2xx` quickly; process async. Failed deliveries retry with backoff (60s → 5m → 15m → 1h), terminal `FAILED` after 5 attempts.
- **Headers:** `X-MIE-Timestamp` (unix seconds), `X-MIE-Signature`.
- **Signature verification (documented recipe):**
  ```
  expected = HMAC_SHA256(secret, f"{timestamp}.{raw_request_body}")  // hex
  reject if timestamp older than 5 minutes (replay protection)
  ```
  `secret` = the developer's `signing_secret` from `/me`.
- **Body shape:**
  ```json
  {
    "event_id": "<uuid — dedupe on this; retries reuse the same id>",
    "type": "SUBMISSION_APPROVED",
    "occurred_at": "2026-08-25T06:00:00+00:00",
    "submission": { "reference": "SCB-0d1c7b2e-A", "status": "APPROVED", "title": "…" }
  }
  ```
  Rejections add `rejection_reason` + `rejection_note`; bypass events add `payout_bypass`.

---

## Error envelope (all 4xx/5xx)

```json
{ "errors": [ { "type": "client_error", "code": "invalid_api_key", "message": "Invalid API key.", "field_name": null } ] }
```

Machine codes worth branching on: `invalid_api_key`, `account_suspended`, `account_not_active`, `token_expired`, `no_credentials`, plus DRF validation codes on 400s (`field_name` is set).

---

## Gotchas checklist for the FE

1. **The API key is shown once** — the approve response is the only chance. Everywhere else it's `api_key_preview` (masked). The **documentation** is different: delivered at the same approval moment, but permanently re-accessible via `/me` (profile) and `/documentation`.
2. **The dev queue cannot be widened** — there is no `developer` filter on `/mie/v1/submissions/queue/` by design; don't build one client-side either.
3. **All statuses appear in queues** — including duplicates and previously-rejected; filter client-side if a screen needs fewer.
4. **Decisions are reversible** — never render APPROVED/REJECTED as terminal; the suffix letter and webhooks will tell you when something moved back.
5. **`payout_bypass` is per-idea**; `plan_type` is per-developer. Both are visible to the developer — render them honestly.
6. **`demand_score` / `estimated_monthly_earnings` are admin-only** inputs (Recommendations queue) — they never appear on dev-facing serializers.
7. **Local/staging `SIGNING_KEY` must be ≥32 bytes** for session tokens (a short key logs a warning and weakens HMAC).
