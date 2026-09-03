# Frontend Handover — Reviewer, Admin & Categories

Everything built for the reviewer, admin/writer and category designs, with
exact response shapes and where to find each endpoint in Swagger.

**Swagger:** `https://<host>/api/v1/docs/` · **Raw schema:** `/api/schema/`

Endpoints are grouped in Swagger by tag, in the order the sidebar shows
them. Each section below names its tag — search that tag in Swagger and
the endpoints are together under it.

---

## Read this first: `null` never means zero

Any metric with nothing recorded behind it returns **`null`**, not `0`.

An unmonitored service is not a healthy one. A catalogue nobody enrolled
on does not have a 0% completion rate. If these defaulted to zero, an
unprobed service would show 100% uptime and read as the healthiest row on
the page.

- **`null`** → render an empty state ("Not monitored", "No data yet", "—")
- **`0`** → a real measurement of nothing

This applies to every dashboard figure below. Money is likewise returned
as **decimal strings**, never floats — parse with a decimal library, not
`parseFloat`, or you will lose precision on fractional-cent costs.

---

# Reviewer

## Dashboard tiles

**Swagger tag:** `Reviewer — Overview`

```
GET /api/v1/reviewer/overview/
```

```json
{
  "courses_reviewed": 245,
  "courses_in_queue": 6,
  "escalations_resolved": 12,

  "queue": { "SUBMITTED": 4, "IN_REVIEW": 2 },
  "my_decisions": { "approved": 31, "today": 3 }
}
```

The first three are the design's tiles. `queue` and `my_decisions` are the
same data broken down and predate this work — safe to ignore, kept so
existing clients don't break.

> **Escalation = appeal.** A disputed rejection is escalated to a senior
> reviewer, so "Escalation resolved" counts appeals this reviewer decided.

## Activity Overview chart

**Swagger tag:** `Reviewer — Overview`

```
GET /api/v1/reviewer/activity-overview/?period=all_time|today|this_week|this_month
```

```json
{
  "period": "this_week",
  "start_date": "2026-08-31",
  "end_date": "2026-09-03",
  "totals": { "escalated": 3, "approved": 12, "rejected": 4 },
  "series": [
    { "date": "2026-08-31", "escalated": 1, "approved": 7, "rejected": 2 },
    { "date": "2026-09-01", "escalated": 2, "approved": 5, "rejected": 2 }
  ]
}
```

- Every day in range is present **including zeroes** — do not fill gaps
  client-side.
- `all_time` runs from the reviewer's first activity. A reviewer with no
  activity gets a single zeroed day, not an empty series.
- An unknown `period` falls back to `today` rather than erroring.
- Scoped to the calling reviewer; no parameter widens it.

## Settings → Account

**Swagger tag:** `Creator — Profile` *(serves every role, not just creators)*

```
GET  /api/v1/users/me/
PATCH /api/v1/users/me/
```

Relevant fields:

```json
{
  "email": "reviewer@example.com",
  "first_name": "Emmanuel",
  "last_name": "Osaite",
  "timezone": "Africa/Lagos",
  "avatar_url": "https://cdn/...",
  "member_since": "2026-04-25T08:00:00Z",
  "role": "CREATOR_REVIEWER",
  "assigned_track": "CREATOR_TRACK"
}
```

- `role` and `assigned_track` are **read-only** — "set by admin" in the
  design. PATCHing them is silently ignored.
- `assigned_track`: `CREATOR_TRACK` | `AI_TRACK` | `ALL` | `null`.
  Labels: "Creator Track" / "AI Track" / "All Tracks".
- Email changes go through `/auth/change-email/`, not here.

> This endpoint previously rejected every non-creator role. It now serves
> all roles and is self-scoped, so no role can read another account.

## Settings → Availability

**Swagger tag:** `Reviewer — Availability`

```
GET   /api/v1/users/me/availability/
PATCH /api/v1/users/me/availability/
```

```json
{
  "id": "uuid",
  "is_available": true,
  "unavailability_reason": "",
  "return_date": null,
  "auto_return_enabled": false,
  "is_effectively_available": true
}
```

| Design label | Field |
|---|---|
| Availability Status | `is_available` |
| Reason for unavailability | `unavailability_reason` |
| Return date | `return_date` |
| Auto-return on return date | `auto_return_enabled` |

## Settings → Queue Behaviour

**Swagger tag:** `Reviewer — Queue Preferences`

```
GET   /api/v1/users/me/queue-preferences/
PATCH /api/v1/users/me/queue-preferences/
```

```json
{
  "id": "uuid",
  "default_sort_order": "ALL",
  "auto_advance_enabled": false,
  "show_ai_track": false,
  "show_creator_track": false,
  "show_both_track": true,
  "effective_track_filter": "ALL"
}
```

**Default sort order** — the six options the design lists:

| Design option | Value | Behaviour |
|---|---|---|
| All | `ALL` *(default)* | No date filter, oldest first |
| Newest First | `NEWEST_FIRST` | No date filter, newest first |
| Oldest First | `OLDEST_FIRST` | No date filter, oldest first |
| Last 30 days | `LAST_30_DAYS` | Last 30 days only, oldest first |
| Last 7 days | `LAST_7_DAYS` | Last 7 days only, oldest first |
| Last 24 hours | `LAST_24_HOURS` | Last 24 hours only, oldest first |

The dropdown mixes ordering and date-window filtering — that is how the
design presents it.

**Track preference** — the three toggles, plus a derived value:

| Toggles set | `effective_track_filter` |
|---|---|
| `show_both_track: true` | `ALL` |
| both single toggles true | `ALL` |
| only `show_ai_track` | `AI_TRACK` |
| only `show_creator_track` | `CREATOR_TRACK` |
| **all three false** | `NONE` |

`effective_track_filter` is **read-only** — send the booleans, read this
back to know what the queue will contain.

> ⚠️ **Handle `NONE`.** All three toggles off is a deliberately empty
> queue. Show an explanation — an unexplained empty queue reads as a bug
> and comes back as a support ticket. `show_both_track` defaults **on** so
> a new reviewer never lands there by accident.

## Settings → Notifications

**Swagger tag:** `Creator — Notifications`

```
GET   /api/v1/users/me/notification-preferences/
PATCH /api/v1/users/me/notification-preferences/
```

| Design label | Field |
|---|---|
| New course assigned to me | `new_course_assigned` |
| Escalation assigned to me | `escalation_assigned` |
| Creator feedback | `creator_feedback` |
| SLA Amber Warning | `sla_amber_warning` |
| SLA Red Critical Alert | `sla_red_critical_alert` |
| SLA Breached | `sla_breached` |
| Amber warning (hours) | `sla_amber_threshold_hours_override` |
| Red Critical Threshold (hours) | `sla_red_threshold_hours_override` |
| In-app notification | `in_app_enabled` |

Both threshold fields are nullable integers — `null` means "use the
platform default".

## Settings → Log in & Security

**Swagger tags:** `Auth — Email Change`, `Auth — Password`

```
POST /api/v1/auth/change-email/
POST /api/v1/auth/change-password/
```

## Settings → Data and Privacy

**Swagger tag:** `Creator — Activity Log`

```
GET /api/v1/users/me/activity-log/export/    → "Download activity log"
GET /api/v1/users/me/audit-log/export/       → "Download audit trail entries"
```

Both stream `text/csv` as an attachment, **not JSON** — trigger a download,
don't parse the body. Both are scoped to the caller server-side.

---

# Admin / Writer

## Overview

**Swagger tag:** `Admin — Overview`

```
GET /api/v1/admin/overview/?period=24h|7d|31d|6m
```

```json
{
  "period": "7d",
  "today": {
    "courses_created_today": 203,
    "courses_created_change_percent": "12.00",
    "published_last_24h": 12,
    "published_total": 156,
    "daily_cost": "1500.0000",
    "daily_cost_change_percent": "2.40",
    "avg_cost_per_course": "5.00"
  },
  "production_trend": [ { "date": "2026-08-28", "count": 12 } ],
  "cost_trend":       [ { "date": "2026-08-28", "amount": "1500.0000" } ],

  "users": {}, "courses": {}, "kyc": {},
  "withdrawals": {}, "wallet_totals": {}
}
```

- Change percentages are **`null` when there is no prior period** — not
  `0`, which would claim flat performance where nothing is comparable.
- Both trends are zero-filled, oldest first, length matching `period`.
- The last five keys predate this work and are unchanged.

## Analytics

**Swagger tag:** `Admin — Analytics`

```
GET /api/v1/admin/analytics/?period=24h|7d|31d|6m
```

```json
{
  "period": "7d",
  "since": "2026-08-27T10:00:00Z",
  "catalog":    { "total_catalog": 2503, "published": 156, "created_in_period": 24 },
  "enrollment": { "total_enrollment": 12400, "enrolled_in_period": 24,
                  "completed": 8100, "avg_completion_rate": 65.6 },
  "cost": {
    "overall_cost": "23500.0000",
    "cost_in_period": "1455.0000",
    "cost_per_course": "5.45",
    "daily":       [ { "date": "2026-08-28", "amount": "1455.0000" } ],
    "by_category": [ { "category": "VOICE", "amount": "800.0000" } ]
  },
  "earnings": { "total_earnings": "5000.00" },
  "distribution": [
    { "channel": "SOLUDESK", "label": "SoluDesk", "count": 235 },
    { "channel": "UDEMY",    "label": "Udemy",    "count": 400 },
    { "channel": "COURSERA", "label": "Coursera", "count": 234 }
  ],
  "production_vs_approval": { "produced": 2500, "approved": 2500, "rejected": 25 },
  "kpis": {
    "daily_output": 200.0,
    "first_pass_approval_percent": 43.6,
    "avg_pipeline_time_minutes": 59.0,
    "cost_per_course": "5.45",
    "review_turnaround_hours": 34.0,
    "system_uptime_percent": 99.91,
    "targets": {
      "daily_output": "200+",
      "first_pass_approval_percent": "≥ 80%",
      "avg_pipeline_time_minutes": "> 60m",
      "cost_per_course": "> $5.00",
      "review_turnaround_hours": "48hr",
      "system_uptime_percent": "99.9%"
    }
  }
}
```

- `kpis.targets` ships **with** the figures — don't hardcode business goals
  you can't see change.
- `distribution` always lists all three channels, including zeroes.
- All money is a decimal string.

## System Health

**Swagger tag:** `Admin — System Health`

```
GET /api/v1/admin/system-health/?window_days=30
```

```json
{
  "window_days": 30,
  "overall_uptime_percent": 99.91,
  "avg_api_latency_ms": 56,
  "avg_recovery_seconds": 14400,
  "degraded_count": 1,
  "down_count": 0,
  "services": [
    {
      "id": "uuid",
      "name": "API Gateway",
      "priority": "MEDIUM",
      "status": "DEGRADED",
      "uptime_percent": 99.9,
      "avg_latency_ms": 590,
      "sample_count": 8640,
      "last_recovery_seconds": 14400
    }
  ]
}
```

`status`: `OPERATIONAL` | `DEGRADED` | `DOWN` | `null`
`priority`: `NORMAL` | `MEDIUM` | `HIGH`

- **`uptime_percent: null` means not monitored, not 100%.** Check
  `sample_count` to judge confidence.
- `last_recovery_seconds` is the time from a service going down to its
  next green reading, measured from the **first** failure of a run — so a
  service flapping for an hour reports an hour, not its final blip. `null`
  when it hasn't failed, or is still down.
- Ten services are pre-registered; readings arrive automatically every
  five minutes.

## APE Pipeline

**Swagger tag:** `Admin — APE Pipeline`

```
GET /api/v1/admin/pipeline/
```

```json
{
  "active_jobs": 203,
  "queue_depth": 100,
  "completed_today": 150,
  "failed_or_retrying": 20,
  "avg_pipeline_seconds": 3540,
  "stages": [
    { "stage": "TOPIC_INTAKE", "label": "Topic Intake",
      "total": 190, "active": 12, "completed": 175, "failed": 3 }
  ],
  "providers": [
    { "id": "uuid", "name": "WellSaid Labs", "kind": "VOICE",
      "load_percent": 89, "queue_depth": 12,
      "readings_updated_at": "2026-09-03T10:04:00Z" }
  ]
}
```

Stages, always all eight in funnel order:
`TOPIC_INTAKE` · `CURRICULUM` · `CONTENT_GENERATION` · `ASSESSMENT_BUILDER`
· `MEDIA_PRODUCTION` · `PREVIEW_VIDEO` · `ASSEMBLY_PACKAGING` · `AUTO_QA`

Providers seeded: WellSaid Labs, Murf AI, Google TTS, Colossyan, Synthesia,
HeyGen. `kind`: `VOICE` | `VIDEO` | `TEXT` | `FALLBACK`.

- Provider readings are **last-known, not live**. Use
  `readings_updated_at` to show staleness; `null` there means never
  polled, which is different from an idle provider.
- Every stage is always present, so the funnel keeps its shape at zero.

## MIE Recommendations

**Swagger tag:** `Admin — MIE Recommendations`

```
GET /api/v1/admin/mie-recommendations/?limit=20
```

```json
{
  "pending_total": 42,
  "scored_total": 18,
  "results": [
    { "id": "uuid", "reference": "SCB-0d1c7b2e-P",
      "title": "Build a Production-Grade Rust Course",
      "developer_email": "dev@studio.io",
      "demand_score": 90,
      "estimated_monthly_earnings": "4200.00",
      "submitted_at": "2026-08-23T08:55:00Z" }
  ]
}
```

Ranked by demand score, then estimated earnings. Unscored ideas sort
**last** but are not hidden — compare `scored_total` against
`pending_total` to show scoring coverage. Read-only; decisions still go
through the MIE admin endpoints.

## Invite staff

**Swagger tag:** `Admin — Staff`

```
POST /api/v1/auth/staff/invitations/    { "email": "...", "role": "STAFF_WRITER" }
```

| Design option | Value |
|---|---|
| Writer | `STAFF_WRITER` |
| Verifier | `STAFF_VERIFIER` |
| Approver | `STAFF_APPROVER` |

## Assign a reviewer's track

**Swagger tag:** `Admin — Users`

```
POST /api/v1/users/admin/{id}/assign-track/   { "assigned_track": "CREATOR_TRACK" }
```

Send `null` to clear. This is the admin-controlled assignment shown
read-only on the reviewer's Account screen — **not** their personal queue
toggles, which they own.

---

# Categories

## List, tabs and stats

**Swagger tags:** `Creator — Categories` (read) · `Admin — Categories` (write)

```
GET /api/v1/categories/
GET /api/v1/categories/stats/
```

```json
{ "total": 205, "active": 200, "inactive": 2, "archived": 3 }
```

One row:

```json
{
  "id": "uuid",
  "name": "Software Engineering",
  "description": "...",
  "creator_price_beginner": "300.00",
  "creator_price_intermediate": "400.00",
  "creator_price_advanced": "500.00",
  "icon": "rocket",
  "track_preference": "CREATOR_PREFERRED",
  "status": "ACTIVE",
  "total_courses": 23,
  "created_datetime": "2026-05-15T15:40:00Z",
  "updated_datetime": "2026-05-15T15:40:00Z"
}
```

Tabs map to filters:

| Tab | Query |
|---|---|
| All | *(none)* |
| Creator Preferred | `?track_preference=CREATOR_PREFERRED` |
| AI Preferred | `?track_preference=AI_PREFERRED` |
| Open | `?track_preference=OPEN` |
| Archive | `?status=ARCHIVED` |

Also supports `?search=` and `?ordering=name,-created_datetime,creator_price_beginner`.

## Create, edit, archive, delete

**Swagger tag:** `Admin — Categories`

```
POST   /api/v1/categories/
PATCH  /api/v1/categories/{id}/
POST   /api/v1/categories/{id}/archive/
POST   /api/v1/categories/{id}/unarchive/
GET    /api/v1/categories/{id}/deletion-impact/
DELETE /api/v1/categories/{id}/
```

Create/edit body:

```json
{
  "name": "Software Engineering",
  "description": "",
  "track_preference": "CREATOR_PREFERRED",
  "creator_price_beginner": "300.00",
  "creator_price_intermediate": "400.00",
  "creator_price_advanced": "500.00",
  "icon": "rocket"
}
```

- **Payout follows course difficulty.** A course freezes the tier matching
  its own `difficulty_level` when it is submitted.
- `icon` is free text — the client owns its icon set.
- Archiving keeps every course, payout and price snapshot; it only removes
  the category from the creator picker, and is reversible.
- Archiving an already-archived category is a **400**, not a silent
  success. Unarchive restores to `ACTIVE`.
- Call `deletion-impact` **before** delete — it reports how many courses
  and profiles would be affected, so the confirmation dialog can warn with
  real numbers.

## Category requests

**Swagger tag:** `Creator — Category Requests`

```
POST /api/v1/category-requests/         { "name": "...", "description": "..." }
GET  /api/v1/category-requests/
POST /api/v1/category-requests/{id}/approve/   { "creator_price": "150000.00" }
POST /api/v1/category-requests/{id}/reject/
```

Creators see their own; admins see all. The category does **not** exist
until approved — don't add it to any picker while `status` is `PENDING`.
On approval all three price tiers start at the supplied rate.

---

# Errors

Every non-2xx uses one envelope:

```json
{ "errors": [ { "type": "validation_error", "code": "required",
                "message": "This field is required.", "field_name": "name" } ] }
```

Branch on `code`, never on `message` — the message is for humans and is not
a stable contract. `errors` is always a list.

| Status | Meaning |
|---|---|
| 400 | Validation failed. `field_name` says which. |
| 401 | Missing/invalid credentials. |
| 403 | Authenticated, wrong role. |
| 404 | Not found, or not yours. |
| 423 | Locked — a module held by another editor. |
| 429 | Rate limited. Respect `Retry-After`. |

---

# Open questions for design

Two tiles could not be built as drawn, because the label and the value
contradict each other:

1. **System Health** has two adjacent tiles both captioned **"Daily cost"**
   — one showing `2 / Degraded`, one showing `$1,500 / 4hr Recover Time`.
   Neither is a cost. Implemented as `degraded_count` and
   `avg_recovery_seconds`; the captions need correcting.
2. **APE Pipeline "Failed / Retry"** shows **`$1,500`** — a currency value
   for a count of failed jobs. Implemented as a count
   (`failed_or_retrying`).

Four sub-captions have no formula yet and are not implemented:

| Tile | Sub-caption | Needs |
|---|---|---|
| Active Jobs | "25 Concurrent instances" | A worker count — nothing tracks concurrency today |
| Queue Depth | "3.2hr Est. to clear" | Depth ÷ throughput; needs completed jobs to establish a rate |
| Completed Today | "On Track for 210+" | A daily target — a business number |
| Failed / Retry | "20 Auto-retrying" | Available now: split `failed_or_retrying` into FAILED and RETRYING |

One product question: the design's sort dropdown omits **SLA urgency**
ordering, which the backend already supports (breached and red-threshold
courses first). It has been removed from the dropdown to match the design,
but the implementation is intact and can be restored as a seventh option.
For a review queue it is arguably the most useful ordering — worth
confirming the omission was deliberate.
