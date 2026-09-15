# MIE Crawler Integration — Implementation Spec

## Purpose of this document

This is a design spec for adding an automated "crawler" idea source to MIE
(Market Intelligence Engine), alongside the existing human/external-developer
idea submission flow. It's written for an engineering agent (human or Claude)
to read, then **explore the actual codebase before writing any code**. Treat
the "Open Questions / Things to Verify" section as mandatory pre-work, not
optional — several decisions below assume things about the existing system
that need to be confirmed against the real implementation, not this doc.

**Core principle: the crawler is just another creator.** It authenticates
with an API key and submits to the same ideas endpoint that external
developers use. It does not get a special backend code path, a separate
review queue, or bypassed deduplication. The only new work is (a) a pipeline
that turns web signals into well-evidenced idea submissions, and (b) small
additive fields on the existing schema so admins can tell crawler ideas
apart and trust them appropriately.

---

## Step 0 — Codebase discovery (do this first)

Before implementing anything, find and read:

1. **Idea submission endpoint** — request/response shape, validation, where
   dedup logic (rejected-before / existing-course / in-queue) lives.
2. **Creator/account model** — registration → pending → approval flow, how
   API keys are generated/hashed/stored, whether "pre-approved" is a
   supported state or needs to be added.
3. **Payout plan model** — how plans are represented (enum? separate table?)
   so a "no payout" plan can be assigned correctly.
4. **Admin dashboard queries** — how submissions are listed/filtered today,
   so a `source` filter can be added consistently rather than bolted on.
5. **Webhook delivery system** — confirm it's keyed off creator account, not
   hardcoded to expect a human — the crawler's webhook target can just be an
   internal logging endpoint or Slack/email alert instead of a real webhook.
6. **Rate limiting / throttling** — does anything like this exist for the
   submission endpoint already? If so, reuse it rather than building a
   parallel limiter.
7. **Idea schema / migrations** — exact current fields, naming conventions,
   and migration tooling in use, so new fields match house style.

Report back what you find before proceeding — some of the design decisions
below (e.g. whether `source` is a new column vs. inferred from creator type)
depend on it.

---

## Architecture overview

```
┌─────────────────┐     ┌──────────────┐     ┌───────────────┐     ┌──────────────┐
│  Source          │────▶│  Signal       │────▶│  Idea          │────▶│  Existing    │
│  Connectors      │     │  Aggregator   │     │  Synthesizer   │     │  Ideas API   │
│  (scheduled)      │     │  & Scorer     │     │  (LLM)         │     │  (unchanged) │
└─────────────────┘     └──────────────┘     └───────────────┘     └──────────────┘
```

The crawler is a **separate, decoupled service** (own repo/deploy target or
clearly isolated module) that talks to Course Builder only through its
public API. It should not import from or directly touch Course Builder's
database, ORM models, or internals. If it breaks, the platform is unaffected.

---

## 1. Source Connectors

Pluggable, independently scheduled jobs. Each outputs a common `RawSignal`
shape regardless of source, so downstream stages don't need per-source
branching.

```json
{
  "source": "job_postings_api",
  "topic_raw": "kubernetes security hardening",
  "signal_strength": 0.78,
  "evidence": { "postings_30d": 940, "growth_pct": 34 },
  "captured_at": "2026-09-14T00:00:00Z"
}
```

Candidate sources (confirm licensing/ToS before building against any of
these — see Open Questions):
- Search trend data (rising queries in relevant categories)
- Job posting APIs (skill mention frequency + growth)
- Community Q&A APIs (question volume/sentiment via official APIs, not scraping)
- Marketplace gap scans (high interest, weak existing course coverage)

No LLM calls at this stage — keep it cheap and fast, pure fetch + normalize.

---

## 2. Aggregator & Scorer

1. Embed `topic_raw` for each signal, cluster by similarity.
2. Merge evidence within a cluster into one candidate.
3. Score using signal strength, source diversity, recency.
4. Drop everything below a submission threshold.

This stage is the main defense against flooding the review queue with noise
— most raw signals should never become a submission. Threshold value is a
tuning parameter, start conservative and adjust based on admin
approval/rejection rate (see Guardrails).

---

## 3. Idea Synthesizer (LLM step)

Only candidates that clear the threshold reach this stage. Produces the
actual submission payload:

```json
{
  "title": "Kubernetes Security Hardening for DevOps Engineers",
  "description": "...",
  "confidence_note": "940 job postings mention this skill (up 34% MoM); 1,200+ related questions on community forums in past 30 days",
  "source_type": "system"
}
```

---

## 4. Submission (reuses existing API unmodified)

- Crawler authenticates as a **pre-approved system creator account** — same
  API key mechanism as human creators, just skips the pending → approval
  wait (needs confirming whether "pre-approved" is trivial to support with
  the existing state machine, or needs a new account state).
- Payout plan: none / non-commercial (map onto whatever the existing payout
  plan model supports — see Step 0).
- Calls the existing `POST /ideas` (or equivalent) endpoint. Gets
  deduplication, webhook notifications, and admin visibility for free.
- Schema additions (additive only, non-breaking):
  - `source_type`: `"human"` | `"system"` (or similar, matching existing
    naming conventions found in Step 0)
  - `confidence_note`: optional free text, shown to admins alongside the
    idea for context
- Webhook target for the system account: internal logging/alerting
  destination rather than a real external URL.

---

## 5. Guardrails (automation-specific)

Nothing paces a bot the way a human naturally does, so these are not
optional:

- **Daily submission cap** on the system account (start low, e.g. 20/day).
- **Circuit breaker**: if admin rejection rate for `source_type: system`
  ideas exceeds a threshold (e.g. 80%) over a rolling window, auto-pause
  submissions and alert — rather than continuing to submit low-quality ideas.
- **Idempotency per topic-cluster per time window** so reruns don't
  resubmit near-duplicate ideas daily (existing platform dedup catches exact
  repeats, but evidence-shifted variants could slip through without this).
- **Source compliance**: respect robots.txt/ToS for every connector, prefer
  official APIs over scraping wherever one exists.

---

## Open Questions / Things to Verify

These need answers — from the codebase, from the team, or both — before or
during implementation:

1. Does the current creator state machine support a "pre-approved" or
   "system" account type without hacking around the pending→approval flow?
2. What payout plan type represents "never paid" today, and is it safe to
   assign to a system account, or does the payout logic assume a human?
3. Is there an existing rate-limiter on the submission endpoint to reuse?
4. What's the actual admin rejection-rate / circuit-breaker mechanism going
   to alert to — Slack, email, PagerDuty? (Depends on what's already wired
   up for other alerts in the system.)
5. Legal/ToS review needed per data source before connectors are built
   against them — this doc does not clear that, it just flags it.
6. What embedding/clustering approach (or library) is consistent with the
   rest of the stack, if one is already in use elsewhere in the platform?
7. Where should the crawler service physically live — same monorepo as a
   separate package, or fully separate repo/deploy? Depends on existing
   infra conventions.

---

## Definition of Done (first version)

- [ ] Codebase discovery findings documented (Step 0)
- [ ] System creator account provisioned, pre-approved, no-payout plan
- [ ] `source_type` + `confidence_note` fields added to idea schema (additive)
- [ ] At least one source connector implemented end-to-end (start with one,
      not all four, to validate the pipeline before scaling sources)
- [ ] Aggregator/scorer with tunable threshold
- [ ] Synthesizer producing valid submissions against the real API in a
      staging environment
- [ ] Daily submission cap enforced
- [ ] Circuit breaker wired to an alert channel
- [ ] Admin dashboard filter by `source_type` (reuses existing filter UI
      patterns found in Step 0)
