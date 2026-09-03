# Admin & Reviewer — Design Match

What the backend now provides for the reviewer, admin/writer and category
designs. Everything below is in Swagger at `/api/v1/docs/`.

**A convention worth reading first.** On these dashboards a metric with
nothing behind it returns **`null`**, not `0`. An unmonitored service is
not a healthy one, and a catalogue nobody enrolled on does not have a 0%
completion rate. Render `null` as an empty state; render `0` as a real
measurement of nothing.

---

## Reviewer

### Already existed — no backend change needed

| Screen | Endpoint |
|---|---|
| Settings → Availability | `GET/PATCH /users/me/availability/` |
| Settings → Notifications | `GET/PATCH /users/me/notification-preferences/` |
| Settings → Log in & Security | `/auth/change-email/`, `/auth/change-password/` |

The Notifications screen is complete as designed: every queue and SLA
toggle, the in-app master switch, and both SLA threshold overrides
(`sla_amber_threshold_hours_override`, `sla_red_threshold_hours_override`).

### Settings → Queue Behaviour — changed to match the design

`GET/PATCH /users/me/queue-preferences/`

**Track preference is now three booleans**, not one enum:
`show_ai_track`, `show_creator_track`, `show_both_track` (defaults on).

The response also carries a derived, read-only `effective_track_filter`
(`ALL` / `AI_TRACK` / `CREATOR_TRACK` / `NONE`) so you do not re-derive
the rule. `show_both_track` wins over the other two.

**Handle `NONE`.** All three toggles off is a deliberately empty queue.
Surface it — an empty queue with no explanation reads as a bug.

**`default_sort_order` now offers the design's six options:**
`ALL` (default), `NEWEST_FIRST`, `OLDEST_FIRST`, `LAST_30_DAYS`,
`LAST_7_DAYS`, `LAST_24_HOURS`. The `LAST_*` values narrow to that window
*and* sort oldest-first. `SLA_URGENCY` was retired — it is not in the
design — but the implementation still exists and can be re-offered.

### Dashboard

`GET /api/v1/reviewer/overview/` now carries the three tiles directly:

```json
{ "courses_reviewed": 245, "courses_in_queue": 6, "escalations_resolved": 12,
  "queue": {...}, "my_decisions": {...} }
```

`queue` and `my_decisions` are unchanged and still present, so nothing
already reading them breaks.

**Escalation = appeal.** A disputed rejection is escalated to a senior
reviewer (PRD §12), so "Escalation resolved" counts appeals this reviewer
decided.

### Activity Overview chart — new

```
GET /api/v1/reviewer/activity-overview/?period=all_time|today|this_week|this_month
```

```json
{ "period": "this_week", "start_date": "...", "end_date": "...",
  "totals": { "escalated": 3, "approved": 12, "rejected": 4 },
  "series": [ { "date": "2026-09-01", "escalated": 2, "approved": 5, "rejected": 2 } ] }
```

Every day in range is present **including zeroes** — do not fill gaps
client-side. `all_time` runs from the reviewer's first activity; with no
activity at all you get a single zeroed day, not an empty chart. An
unknown `period` falls back to `today` rather than erroring.

### Account screen

`GET/PATCH /users/me/` now works for **every role**. It was previously
restricted to self-registered creators, which 403'd reviewers and made
the Account screen impossible. It is self-scoped, so no role can read
another account.

New read-only field `assigned_track` (`CREATOR_TRACK` / `AI_TRACK` /
`ALL` / null). Admins set it with:

```
POST /api/v1/users/admin/{id}/assign-track/   { "assigned_track": "CREATOR_TRACK" }
```

Do not confuse it with `queue-preferences.track_filter` — that is the
reviewer's own filter, this is the assignment they cannot change.

### Data and Privacy — both buttons

```
GET /api/v1/users/me/activity-log/export/   (existed)
GET /api/v1/users/me/audit-log/export/      (new)
```

Both stream `text/csv` as attachments. The audit export is scoped to the
caller's own entries server-side.

---

## Admin / Writer

Three new screens, backed by a new `operations` domain rather than
invented figures.

### Analytics

```
GET /api/v1/admin/analytics/?period=24h|7d|31d|6m
```

Returns `catalog`, `enrollment`, `cost`, `distribution`,
`production_vs_approval` and `kpis`. **Money is returned as decimal
strings**, never floats, so nothing is lost in transit — parse with a
decimal library, not `parseFloat`.

`enrollment.avg_completion_rate`, `cost.*` and
`kpis.first_pass_approval_percent` are null until there are enrolments,
recorded costs and review decisions respectively.

### System Health

```
GET /api/v1/admin/system-health/?window_days=30
```

Per-service `status`, `uptime_percent`, `avg_latency_ms`, `sample_count`,
plus overall tiles. Uptime is computed from samples in the window, never
stored, so it cannot go stale.

**A service with no samples reports `uptime_percent: null`, not 100%.**
Show that as "not monitored". `sample_count` tells you how much
confidence the number deserves.

**Recovery time** — `avg_recovery_seconds` and
`services[].last_recovery_seconds` — is the interval from a service
going down to its next operational sample, measured from the *first*
failure of a run. A service still down reports `null`: it has not
recovered yet.

Needs something writing `ServiceHealthSample` rows — register services,
then run a probe. Without one, every row is honestly null.

### APE Pipeline

```
GET /api/v1/admin/pipeline/
```

Tiles (`active_jobs`, `queue_depth`, `completed_today`,
`failed_or_retrying`), a `stages` array with **every stage in funnel
order including zeroes**, and `providers` with last-known load and queue.

Provider readings are **not live** — `readings_updated_at` tells you how
stale they are, and is null for a provider never polled. Distinguish that
from an idle provider.

### MIE Recommendations

```
GET /api/v1/admin/mie-recommendations/?limit=20
```

Pending partner ideas ranked by `demand_score`, then estimated earnings.
Unscored ideas sort **last** but are not hidden — compare `scored_total`
against `pending_total` to see how much of the queue has been assessed.
Read-only; deciding an idea still goes through the MIE admin endpoints.

### Overview

`GET /api/v1/admin/overview/` gains `today`, `production_trend` and
`cost_trend` alongside its existing blocks.

Change percentages are **null when there is no prior period** — showing 0
would claim flat performance where nothing is comparable.

Both trend arrays are 7 days, zero-filled, oldest first.

---

## Categories

### Three payout levels

`creator_price` is replaced by three fields keyed to course difficulty:

```json
{ "creator_price_beginner": "500.00",
  "creator_price_intermediate": "520.00",
  "creator_price_advanced": "600.00" }
```

A course freezes the tier matching **its own `difficulty_level`** at
submission. A topic-specific price still overrides the category, as
before.

Existing categories were migrated with all three tiers set to the old
single price, so **no creator's pay changed**. Differentiating a tier is
a deliberate admin edit.

### New fields and actions

- `icon` — free-text identifier; the client owns its icon set.
- `total_courses` on list rows, annotated (one query, not N+1).
- `status` gains `ARCHIVED` alongside `ACTIVE` / `INACTIVE`.

```
GET  /api/v1/categories/stats/            → { total, active, inactive, archived }
POST /api/v1/categories/{id}/archive/
POST /api/v1/categories/{id}/unarchive/
```

Archiving keeps every course, payout and price snapshot — it only removes
the category from the creator picker, and is reversible. Archiving an
already-archived category is a **400**, not a silent success. Unarchive
restores to `ACTIVE`, not to whatever status preceded archiving.

Tabs map to existing filters: `?track_preference=CREATOR_PREFERRED|AI_PREFERRED|OPEN`
and `?status=ARCHIVED`.
