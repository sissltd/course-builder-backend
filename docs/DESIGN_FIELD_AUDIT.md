# Design → API Field Audit

Every label in the supplied designs mapped to the field the API actually
returns, generated from the live OpenAPI schema. **Two genuine mismatches
are listed at the bottom** — both need a product decision, neither is a
silent difference.

---

## Reviewer — Dashboard

| Design label | API field | ✓ |
|---|---|---|
| Courses Reviewed | `courses_reviewed` | ✓ |
| Courses in Queue | `courses_in_queue` | ✓ |
| Escalation resolved | `escalations_resolved` | ✓ |
| Activity Overview → Escalated | `series[].escalated` | ✓ |
| Activity Overview → Approved | `series[].approved` | ✓ |
| Activity Overview → Rejected | `series[].rejected` | ✓ |
| All time / Today / This week / This month | `?period=all_time\|today\|this_week\|this_month` | ✓ |

`GET /api/v1/reviewer/overview/` · `GET /api/v1/reviewer/activity-overview/`

## Reviewer — Settings → Account

| Design label | API field | ✓ |
|---|---|---|
| Member since | `member_since` | ✓ |
| Role → "Reviewer" (set by admin) | `role` (read-only) | ✓ |
| Assigned Track → "Creator Track" | `assigned_track` → `CREATOR_TRACK` (label "Creator Track") | ✓ |
| Enter email adress | `email` | ✓ |
| First name / Last name | `first_name` / `last_name` | ✓ |
| Timezone | `timezone` | ✓ |
| Upload / delete avatar | `avatar_url` | ✓ |

## Reviewer — Settings → Availability

| Design label | API field | ✓ |
|---|---|---|
| Availability Status | `is_available` | ✓ |
| Reason for unavailability | `unavailability_reason` | ✓ |
| Return date | `return_date` | ✓ |
| Auto-return on return date | `auto_return_enabled` | ✓ |

## Reviewer — Settings → Queue Behaviour

| Design label | API field | ✓ |
|---|---|---|
| Default sort order | `default_sort_order` | ⚠️ **see M1** |
| Auto-advance on decision | `auto_advance_enabled` | ✓ |
| Show AI track courses | `track_filter = AI_TRACK` | ✓ |
| Show creator track courses | `track_filter = CREATOR_TRACK` | ✓ |
| Show both track | `track_filter = ALL` | ⚠️ **see M2** |

## Reviewer — Settings → Notifications

| Design label | API field | ✓ |
|---|---|---|
| New course assigned to me | `new_course_assigned` | ✓ |
| Escalation assigned to me | `escalation_assigned` | ✓ |
| Creator feedback | `creator_feedback` | ✓ |
| SLA Amber Warning | `sla_amber_warning` | ✓ |
| SLA Red Critical Alert | `sla_red_critical_alert` | ✓ |
| SLA Breached | `sla_breached` | ✓ |
| Amber warning (36h) | `sla_amber_threshold_hours_override` | ✓ |
| Red Critical Threshold (36h) | `sla_red_threshold_hours_override` | ✓ |
| In-app notification | `in_app_enabled` | ✓ |

## Reviewer — Settings → Data and Privacy

| Design label | Endpoint | ✓ |
|---|---|---|
| Download activity log | `GET /users/me/activity-log/export/` | ✓ |
| Download audit trail entries | `GET /users/me/audit-log/export/` | ✓ |

---

## Admin — Overview

| Design label | API field | ✓ |
|---|---|---|
| 24 hrs / 7 days / 31 days / 6 months | `?period=24h\|7d\|31d\|6m` | ✓ |
| Courses Created Today | `today.courses_created_today` | ✓ |
| ↑ 12% since yesterday | `today.courses_created_change_percent` | ✓ |
| Published Courses | `today.published_total` | ✓ |
| Last 24hrs | `today.published_last_24h` | ✓ |
| Daily cost | `today.daily_cost` | ✓ |
| +2.4% this week | `today.daily_cost_change_percent` | ✓ |
| Avg $5 per course | `today.avg_cost_per_course` | ✓ |
| Production Trend | `production_trend[]` | ✓ |
| Average production cost | `cost_trend[]` | ✓ |

## Admin — Invite staff

| Design option | API value | ✓ |
|---|---|---|
| Writer | `STAFF_WRITER` (label "Writer") | ✓ |
| Verifier | `STAFF_VERIFIER` (label "Verifier") | ✓ |
| Approver | `STAFF_APPROVER` (label "Approver") | ✓ |

## Admin — Analytics

| Design label | API field | ✓ |
|---|---|---|
| Total Catalog | `catalog.total_catalog` | ✓ |
| Total Enrollment | `enrollment.total_enrollment` | ✓ |
| Avg Completion Rate | `enrollment.avg_completion_rate` | ✓ |
| Overall Cost | `cost.overall_cost` | ✓ |
| Total Earnings | `earnings.total_earnings` | ✓ |
| Distribution → Soludesk / Udemy / Coursera | `distribution[]` → `SOLUDESK` / `UDEMY` / `COURSERA` | ✓ |
| Production vs Approval → Approved | `production_vs_approval.approved` | ✓ |
| Production vs Approval → Produced | `production_vs_approval.produced` | ✓ |
| Production vs Approval → Rejected | `production_vs_approval.rejected` | ✓ |
| KPI → Daily output (Target 200+) | `kpis.daily_output` + `kpis.targets.daily_output` | ✓ |
| KPI → First-pass Approval (≥ 80%) | `kpis.first_pass_approval_percent` | ✓ |
| KPI → Avg Pipeline Time (> 60m) | `kpis.avg_pipeline_time_minutes` | ✓ |
| KPI → Cost Per Course (> $5.00) | `kpis.cost_per_course` | ✓ |
| KPI → Review Turnaround (48hr) | `kpis.review_turnaround_hours` | ✓ |
| KPI → System Uptime (99.9%) | `kpis.system_uptime_percent` | ✓ |

Targets ship in `kpis.targets`, keyed by the figure they belong to.

## Admin — System Health

| Design label | API field | ✓ |
|---|---|---|
| Overall Uptime | `overall_uptime_percent` | ✓ |
| AVG API Latency | `avg_api_latency_ms` | ✓ |
| Degraded | `degraded_count` | ✓ |
| Table → service name | `services[].name` | ✓ |
| Table → Status (Operational/Degraded) | `services[].status` | ✓ |
| Table → Up Time | `services[].uptime_percent` | ✓ |
| Table → latency | `services[].avg_latency_ms` | ✓ |
| Table → Priority (Normal/Medium) | `services[].priority` | ✓ |

All ten services in the design are seeded by migration: Creator Studio,
API Gateway, APE Pipeline, MIE Crawler, PostgreSQL, Redis Cache, S3/CDN,
WellSaid TTS, Colossyan Video, Intron Sahara.

| 4hr Recover Time | `avg_recovery_seconds` + `services[].last_recovery_seconds` | ✓ |

**Recovery time** is measured as the interval from the moment a service
stopped being operational to the first operational sample after it —
measured from the *first* failure of a run, not the last, so a service
that flaps for an hour reports an hour rather than the few seconds of its
final blip. A service still down reports `null`: it has not recovered, and
including a partial figure would drag the average down and imply things
are better than they are.

## Admin — APE Pipeline

| Design label | API field | ✓ |
|---|---|---|
| Active Jobs | `active_jobs` | ✓ |
| Queue Depth | `queue_depth` | ✓ |
| Completed Today | `completed_today` | ✓ |
| Failed / Retry | `failed_or_retrying` | ✓ |
| Topic Intake | `TOPIC_INTAKE` → "Topic Intake" | ✓ |
| Curriculum | `CURRICULUM` → "Curriculum" | ✓ |
| Content Generation | `CONTENT_GENERATION` | ✓ |
| Assessment Builder | `ASSESSMENT_BUILDER` | ✓ |
| Media Production | `MEDIA_PRODUCTION` | ✓ |
| Preview Video | `PREVIEW_VIDEO` | ✓ |
| Assembly and Packaging | `ASSEMBLY_PACKAGING` → "Assembly and Packaging" | ✓ |
| Auto-QA | `AUTO_QA` → "Auto-QA" | ✓ |
| Load: 89% | `providers[].load_percent` | ✓ |
| Queue: 12 | `providers[].queue_depth` | ✓ |

All six providers seeded: WellSaid Labs (Voice), Murf AI (Voice), Google
TTS (Fallback), Colossyan (Video), Synthesia (Video), HeyGen (Video).

#### Four sub-captions with no definition — for the FE to confirm

These sit as small grey text **under** the four tiles at the top of the
APE Pipeline screen (third screenshot, "Pending" frame). Each needs a
formula before it can be measured honestly:

| Tile | Sub-caption under it | What it would need |
|---|---|---|
| Active Jobs — 203 | "25 Concurrent instances" | A worker/instance count. Nothing tracks concurrency; the pipeline records jobs, not the machines running them. |
| Queue Depth — 100 | "3.2hr Est. to clear" | Queue depth ÷ recent throughput. Computable once enough completed jobs exist to establish a rate — meaningless until then. |
| Completed Today — 150 | "On Track for 210+" | A daily target, plus a projection from today's rate. The target is a business number nobody has supplied. |
| Failed / Retry — $1,500 | "20 Auto-retrying" | Already available: split `failed_or_retrying` into `FAILED` and `RETRYING`. A one-line change whenever you want it. |

Also worth flagging to the FE: **the "Failed / Retry" tile shows
`$1,500`** — a currency value for what is a count of failed jobs. That
looks like a copy-paste from the cost tiles in the Overview design rather
than an intended figure. Same pattern appears on **System Health**, where
two adjacent tiles are both labelled "Daily cost" while showing a
degraded-service count and a recovery time.

## Categories

| Design label | API field | ✓ |
|---|---|---|
| Total Category | `stats.total` | ✓ |
| Active Category | `stats.active` | ✓ |
| Archived Category | `stats.archived` | ✓ |
| Tab: Creator Preferred | `?track_preference=CREATOR_PREFERRED` | ✓ |
| Tab: AI Preferred | `?track_preference=AI_PREFERRED` | ✓ |
| Tab: Open | `?track_preference=OPEN` | ✓ |
| Tab: Archive | `?status=ARCHIVED` | ✓ |
| Column: Category | `name` | ✓ |
| Column: Track | `track_preference` | ✓ |
| Price: Beg $300 | `creator_price_beginner` | ✓ |
| Price: Int $400 | `creator_price_intermediate` | ✓ |
| Price: Adv $500 | `creator_price_advanced` | ✓ |
| Column: Total Courses | `total_courses` | ✓ |
| Column: Date created | `created_datetime` | ✓ |
| Modal: Category name | `name` | ✓ |
| Modal: Track preference | `track_preference` | ✓ |
| Modal: Payout price level | the three price fields | ✓ |
| Modal: Select category icon | `icon` | ✓ |
| Action: Edit category | `PATCH /categories/{id}/` | ✓ |
| Action: Archive category | `POST /categories/{id}/archive/` | ✓ |
| Action: Delete category | `DELETE /categories/{id}/` | ✓ |

---

## The two genuine mismatches

### M1 — Sort order — **resolved, now matches the design**

`QueueSortOrder` previously had three values (`OLDEST_FIRST`,
`NEWEST_FIRST`, `SLA_URGENCY`), only two of which the design offered. It
now carries exactly the design's six:

| Design option | API value |
|---|---|
| All | `ALL` (default) |
| Newest First | `NEWEST_FIRST` |
| Oldest First | `OLDEST_FIRST` |
| Last 30 days | `LAST_30_DAYS` |
| Last 7 days | `LAST_7_DAYS` |
| Last 24 hours | `LAST_24_HOURS` |

The dropdown mixes two concerns, which is how the design presents it:
`ALL` / `OLDEST_FIRST` / `NEWEST_FIRST` change the **ordering**, while the
three `LAST_*` values narrow to that **window** and then sort
oldest-first.

**One capability was retired.** `SLA_URGENCY` — which ranked breached and
red-threshold courses first — is not in the design and is no longer
selectable. The implementation was **not deleted**:
`course_service.get_review_queue` still honours the string, so it can be
put back as a seventh option whenever you want it. Worth raising with the
FE, since for a review queue it is arguably the most useful ordering of
the seven.

### M2 — Track preference — **resolved, now three toggles**

`track_filter` is replaced by the three booleans the design shows:

| Design toggle | API field |
|---|---|
| Show AI track courses | `show_ai_track` |
| Show creator track courses | `show_creator_track` |
| Show both track | `show_both_track` (defaults **on**) |

Three independent switches make some combinations ambiguous, so the API
also returns a derived, read-only `effective_track_filter` telling the
client what the queue will actually contain:

| Toggles | `effective_track_filter` |
|---|---|
| `show_both_track` on | `ALL` |
| both single toggles on | `ALL` |
| only AI on | `AI_TRACK` |
| only creator on | `CREATOR_TRACK` |
| **all three off** | `NONE` — an intentionally empty queue |

`NONE` is represented rather than quietly coerced to `ALL`: a reviewer who
switched every track off asked for an empty queue, and silently showing
them everything would be the wrong answer. **The FE should surface that
state** — an empty queue with no explanation looks like a bug.

`show_both_track` defaults on so a new reviewer never opens an empty
queue.

### Minor naming note

The design tile reads **"Escalation resolved"** (singular); the field is
`escalations_resolved` (plural), which is the accurate name for a count
and matches the rest of the API's pluralisation. Easy to change if the FE
would rather mirror the label exactly.
