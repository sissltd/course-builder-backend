# Reviewer / Approver Dashboard — Action Endpoints

Handover doc for the frontend team. Covers the shared dashboard used by
**Reviewer(s)**, **QA Reviewer**, and **Approver**, and — critically — the
**action endpoints** that drive the buttons, which are separate routes from
the GET table screens.

**TL;DR:** The four dashboard tables are GET-only and shared by every role.
All state changes (claim, approve, reject, publish) go through dedicated
`POST`/`PUT` sub-routes on the same `/api/v1/review-queue/` resource. The
backend decides what each role may do — the frontend does not need to hide
or gate buttons by role (it may for UX, but the API enforces the rules).

---

## 1. Roles and what each one does

| Role value | Dashboard persona | What they can do |
|---|---|---|
| `CREATOR_REVIEWER`, `STAFF_VERIFIER` | Reviewer 1 / Reviewer 2 (content review) | Claim, approve content, reject (with feedback), comment, set prices, publish |
| `QA_REVIEWER` | QA reviewer | QA-claim, QA-approve, QA-reject, comment |
| `STAFF_APPROVER` | Approver | Publish approved courses (treated as Admin-tier for review actions too) |
| `ADMIN`, `SUPER_ADMIN` | (outranks all) | Everything above |

Roles are JWT-carried; every endpoint below returns `403` for a caller whose
role is not allowed, and `400` when the course is in a state that does not
permit the action (exact messages in §6).

## 2. Course status machine

```
DRAFT ──submit──▶ SUBMITTED ──claim──▶ IN_REVIEW ──approve──▶ QA_VERIFICATION
                                                                    │
                                             qa-claim │             │
                                                      ▼             │
                                                 (QA stage) ──qa-approve──▶ APPROVED ──publish──▶ PUBLISHED

SUBMITTED/IN_REVIEW ──reject──▶ DRAFT          QA_VERIFICATION ──qa-reject──▶ DRAFT
```

Status values (as returned in every course payload):
`DRAFT`, `SUBMITTED`, `IN_REVIEW`, `NEEDS_REVISION`, `QA_VERIFICATION`,
`APPROVED`, `PUBLISHED`, `ARCHIVED`, `REJECTED`.

Note: a rejection never persists `REJECTED` on the course — it reverts the
course to `DRAFT` and records the decision in a `ReviewAction` (the course
keeps `rejected_at` set so the creator dashboard can show why).

## 3. The shared GET tables (already implemented)

All four sidebar tables are on one viewset, share the same row shape, and are
**read-only**:

```
GET /api/v1/review-queue/pending/     → SUBMITTED courses
GET /api/v1/review-queue/in-review/   → IN_REVIEW courses
GET /api/v1/review-queue/approved/    → APPROVED courses
GET /api/v1/review-queue/published/   → PUBLISHED courses
GET /api/v1/review-queue/             → all of the above (filterable via ?status=)
GET /api/v1/review-queue/{id}/        → drawer detail (review_information,
                                        owner_information, price_information blocks)
```

Every user with any reviewer/approver/admin role sees the same tables — the
dashboard is shared. See `FRONTEND_HANDOVER.md` for row fields and filters.

---

## 4. Action endpoints — content review (Reviewer 1 & 2)

All are `POST` under `/api/v1/review-queue/{course_id}/…`.

### 4.1 Claim a course — `POST /api/v1/review-queue/{id}/claim/`

- **Who:** Creator Reviewer, Verifier, Admin.
- **When:** Reviewer opens a Submitted course and starts reviewing.
- **Prerequisite:** course is `SUBMITTED` (or already `IN_REVIEW` — idempotent
  for the reviewer who already holds it; a *different* reviewer gets `400`).
- **Request body:** none.
- **Response `200`:** the full course detail with `status: "IN_REVIEW"`.

```json
{ "id": "…", "status": "IN_REVIEW", "title": "Lorem 1", … }
```

### 4.2 Approve content — `POST /api/v1/review-queue/{id}/approve/`

Aliases: `POST …/content-approve/` (identical behaviour).

- **Who:** Creator Reviewer, Verifier, Admin.
- **When:** The **Approve** button on the review screen.
- **Prerequisite:** course is `SUBMITTED` or `IN_REVIEW`; reviewer not marked
  Unavailable.
- **Effect:** records a `ReviewAction(APPROVE, stage=CONTENT)`, moves the
  course to `QA_VERIFICATION`, runs baseline quality checks if none exist,
  notifies the creator. **No wallet credit and no publication yet.**
- **Request body (optional):**

```json
{ "feedback": { "summary": "Looks great, approved as-is." } }
```

- **Response `200`:**

```json
{
  "id": "review-action-uuid",
  "course": "course-uuid",
  "reviewer": "reviewer-uuid",
  "action": "APPROVE",
  "stage": "CONTENT",
  "feedback": { "summary": "Looks great, approved as-is." },
  "created_datetime": "2026-09-13T10:00:00Z"
}
```

### 4.3 Reject a course — `POST /api/v1/review-queue/{id}/reject/`

Aliases: `POST …/content-reject/`.

- **Who:** Creator Reviewer, Verifier, Admin.
- **When:** The **Reject** button.
- **Prerequisite:** course is `SUBMITTED` or `IN_REVIEW`; reviewer not marked
  Unavailable.
- **Effect:** records a `ReviewAction(REJECT, stage=CONTENT)` (plus any
  structured flags), **reverts the course to `DRAFT`** for revision, notifies
  the creator with the feedback.
- **Request body (required — `feedback.summary` must be non-empty):**

```json
{
  "feedback": {
    "summary": "Lesson 2 script needs more detail.",
    "items": [
      { "module_id": "…", "lesson_id": "…", "comment": "Add examples." }
    ]
  },
  "flags": [
    {
      "flag_type": "CONTENT_GAP",
      "title": "Lesson 2 too thin",
      "system_message": "Lesson script under minimum length.",
      "reviewer_note": "Please expand with worked examples.",
      "lesson_id": "…",
      "module_id": "…"
    }
  ]
}
```

`items` entries require `module_id` and `comment`; flag entries require
`flag_type` and `title` and `lesson_id`/`module_id` must belong to the
reviewed course (anything else → `400`, nothing is saved).
- **Response `200`:** the `ReviewAction` (same shape as approve, with
  `"action": "REJECT"`).

### 4.4 Review comments — `GET|POST /api/v1/review-queue/{id}/comments/`

- **Who:** any review/admin role.
- `GET` lists all comments on the course; `POST` adds one:

```json
{
  "stage": "CONTENT",
  "module": null,
  "lesson": "lesson-uuid-or-null",
  "severity": "WARNING",
  "reason_code": "SHORT_SCRIPT",
  "comment": "Please expand this lesson."
}
```

- **Response `201`:** the created comment (with `reviewer` populated).

---

## 5. Action endpoints — QA verification (QA Reviewer)

### 5.1 Claim QA — `POST /api/v1/review-queue/{id}/qa-claim/`

- **Who:** QA Reviewer, Admin. **Prerequisite:** course is
  `QA_VERIFICATION`. Idempotent for the holder; another QA reviewer gets
  `400` ("already assigned").
- **Request body:** none. **Response `200`:** course detail.

### 5.2 QA approve — `POST /api/v1/review-queue/{id}/qa-approve/`

- **Who:** QA Reviewer, Admin. **Prerequisite:** course is
  `QA_VERIFICATION` **and** every required media asset is registered and
  passing (otherwise `400` with the failing media list).
- **Effect:** records `ReviewAction(APPROVE, stage=QA)`, moves the course to
  `APPROVED`, **credits the creator's wallet** for creator-uploaded courses.
- **Request body (optional):** `{ "feedback": { … } }`
- **Response `200`:** the `ReviewAction`.

### 5.3 QA reject — `POST /api/v1/review-queue/{id}/qa-reject/`

- **Who:** QA Reviewer, Admin. **Prerequisite:** course is
  `QA_VERIFICATION`.
- **Effect:** reverts the course to `DRAFT` (creator fixes media/accessibility).
- **Request body (required):** `{ "feedback": { "summary": "…" } }`

---

## 6. Action endpoints — Approver (publish flow)

### 6.1 Read pricing tabs — `GET /api/v1/review-queue/{id}/review-prices/`

- **Who:** Creator Reviewer, Verifier, Admin. **Prerequisite:** course is
  `APPROVED` (a Draft course → `400`).
- **Response `200`:** array of saved channel pricing tabs (SoluDesk,
  Coursera, Udemy — the three Figma Review modal frames).

### 6.2 Save pricing tabs — `PUT /api/v1/review-queue/{id}/review-prices/`

```json
{
  "distribution_channels": [
    {
      "channel": "SOLUDESK",
      "learner_price": "149.00",
      "mie_suggestion": "140.00",
      "model": "ONE_TIME",
      "platform_revenue_per_enrollment": "149.00",
      "mie_explanation": "Suggested from competitor analysis.",
      "comparable_courses": [
        { "course_title": "Modern computing language",
          "difficulty_level": "BEGINNER", "learner_price": "150.00" }
      ]
    },
    {
      "channel": "UDEMY",
      "learner_price": "190.00",
      "model": "ONE_TIME",
      "course_fee_percent": "32.00",
      "promotional_pricing": "150.00"
    }
  ]
}
```

- Rules: each channel at most once, at least one channel; money values are
  decimal **strings**. `creator_payout_fixed` is read-only (comes from the
  submission price snapshot).
- **Response `200`:** the saved channel rows.

### 6.3 Publish — `POST /api/v1/review-queue/{id}/publish/`

> **The Approver's publish button.**

- **Who:** Creator Reviewer, Verifier, Admin (i.e. `STAFF_APPROVER` passes via
  the Admin-tier check).
- **Note:** today the backend allows *reviewers* to publish as well — the
  role split ("reviewers approve content, only the Approver publishes") is a
  UI convention, not an API constraint. If product wants it enforced,
  that's a one-line backend permission change; until then, hide the Publish
  button for reviewer-only users in the UI if the design requires it.
- **Prerequisite:** course is `APPROVED` (anything else → `400`).
- **Request body (optional):** the same `distribution_channels` payload as
  §6.2 — supply it to set prices at publish time; omit it to publish with
  previously saved prices.
- **Effect:** sets the course to `PUBLISHED` (stamps `published_at`), creates
  per-channel `CourseDistribution` rows — SoluDesk becomes `PUBLISHED`
  locally; Udemy/Coursera are recorded as `QUEUED` pending their external
  integrations — and logs a `COURSE_PUBLISHED` activity.
- **Response `200`:** the course detail, `status: "PUBLISHED"`, plus
  `channels` listing the channels that were published:

```json
{ "status": "PUBLISHED", "channels": ["SOLUDESK", "UDEMY"], … }
```