# MIE Crawler — Frontend Handover

Additive change to the MIE admin surfaces. **No new endpoints, no new screens,
no breaking changes.** Four new fields, one new filter. Read
[mie_frontend_guide.md](mie_frontend_guide.md) first if you haven't built the
MIE screens yet; this only covers what the crawler adds.

---

## Part 1 — The logic

### What the crawler is

A piece of software the platform owns that finds course ideas on the open web
(job postings, search trends, community questions) and submits them. It is
**not** a special backend path: it authenticates with an API key and calls the
same submission endpoint an external partner does, gets the same
deduplication, and lands in the same admin review queue.

So from your side, the crawler is just another developer account — one you can
now tell apart from the human ones.

### The two account kinds

Every developer account carries a `source_type`:

| Value | Meaning |
|---|---|
| `EXTERNAL` | A third-party developer. Every account that predates the crawler. |
| `SYSTEM` | The platform's own crawler. |

A submission's `source_type` is the source type of the account that submitted
it. It is read-only everywhere and cannot be set through the API: a registration
that tries to claim `SYSTEM` still lands as `EXTERNAL`, and only a management
command creates a `SYSTEM` account.

### The crawler's evidence

A crawler submission may carry `confidence_note`: free text, up to 2,000
characters, explaining why it thinks the idea is worth building — typically
the signal counts behind it. For example:

> 940 job postings mention this skill (up 34% month on month); 1,200+ related
> questions on community forums in the past 30 days

It is optional, may be empty, and external partners are allowed to send it too.
Treat it as evidence shown to the reviewing admin, not as a title or summary.

### Two things that can happen to a crawler account

Both apply to `SYSTEM` accounts **only**. External partners are never affected,
so nothing you already built changes behaviour.

1. **A daily cap.** At most 20 submissions in any rolling 24 hours. Past it the
   crawler gets `429` with `Retry-After` and nothing is stored.
2. **A rejection circuit breaker.** If admins reject 80% or more of at least 10
   decisions within 7 days, the account is **suspended automatically**. Every
   super admin gets an in-app notification and an email. An admin reverses it
   with the existing approve action on the developer.

The consequence for your UI: a developer's status can change to `SUSPENDED`
**without anyone clicking anything**. Don't assume a suspension implies an
admin action.

---

## Part 2 — What you need to do

### 1. Show the source on submission rows

`GET /api/v1/mie/admin/submissions/` and `.../{id}/` now return:

| Field | Type | Notes |
|---|---|---|
| `source_type` | `"EXTERNAL"` \| `"SYSTEM"` | Read-only. Never null. |
| `confidence_note` | string | Read-only. May be `""`. |

Label the row so an admin can tell at a glance where an idea came from — a
badge on the title is enough. Don't call it "bot" in the UI; "System" and
"External" match the API.

### 2. Add the source filter to the queue

```
GET /api/v1/mie/admin/submissions/?source_type=SYSTEM
GET /api/v1/mie/admin/submissions/?source_type=EXTERNAL
```

Combines with the existing filters (`status`, `developer`, `email`,
`payout_bypass`, `created_after`, `created_before`, `search`). Send nothing to
get both, as today.

**An unknown value returns `400`** — it is not ignored. So drive the control
from the two values above rather than free text.

### 3. Render `confidence_note` on the submission detail

Show it near the payload, presented as the submitter's evidence — a quoted
block or a labelled panel, not a paragraph mixed into the description. Hide the
block when it's empty rather than rendering an empty label. Expect multi-line
text and preserve line breaks.

### 4. Show the source on the developers list

`GET /api/v1/mie/admin/developers/` and `.../{id}/` now return `source_type`
with the same two values, read-only. Useful to mark which row is the crawler,
since it otherwise looks like any other approved account.

### 5. Show the source on recommendations

`GET /api/v1/admin/mie-recommendations/` rows now carry `source_type` too, so
an admin ranking ideas by demand score can see which were machine-suggested.
Note this route lives outside the `/mie/` prefix and admits **Admin as well as
Super Admin**, unlike the routes above.

### 6. Handle automatic suspension

Because the breaker can suspend the crawler on its own:

- Re-read the developer's `status` after any queue action rather than caching
  it for the session.
- Surface the in-app notification that announces it — it names the account and
  its rejection rate. It goes to super admins only.
- The recovery path is the existing approve action on that developer. No new
  endpoint, no special "unsuspend".

### 7. Nothing to build for the cap

The `429` + `Retry-After` on submission is between the platform and the
crawler. The admin UI never submits ideas, so unless you are also building a
developer-side submission screen, there is nothing to handle here.

---

## Summary

| Surface | Change | Breaking? |
|---|---|---|
| Admin submissions list + detail | `source_type`, `confidence_note` added | No — additive |
| Admin submissions list | `?source_type=` filter added (invalid value → `400`) | No |
| Admin developers list + detail | `source_type` added | No |
| Admin recommendations | `source_type` added | No |
| Developer status | can become `SUSPENDED` with no admin action | Behavioural, no contract change |
| Submission ingest | optional `confidence_note`; `429` for system accounts at the cap | No — additive |

Every field above is read-only. Auth is unchanged: the `/mie/admin/…` routes
take a Super Admin's platform JWT and still reject API keys, while
`/admin/mie-recommendations/` admits Admin or Super Admin.
