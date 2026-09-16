# MIE Crawler

## What is the crawler?

MIE already lets external people submit course ideas and get paid when an idea
is accepted (see [mie_overview.md](mie_overview.md)). The crawler is the same
idea with the human removed: a piece of software the platform owns that watches
the open web for signals of demand — job postings, search trends, community
questions — and submits the strongest ones as course ideas.

The important design decision is what the crawler *isn't*. It is not a special
backend feature. It is **an ordinary developer account that happens to be a
robot.** It registers, gets approved, receives an API key, calls the same
submission endpoint, is deduplicated by the same three checks, and appears in
the same admin review queue as any external partner.

That choice buys three things. There is no second code path to maintain and no
second queue to review. If the crawler breaks, the platform is unaffected —
its submissions simply stop. And a crawler idea cannot skip the human review
that every other idea passes through.

---

## Who is it for?

**Platform administrators.** Their queue gains a second source of ideas. Every
submission is labelled with where it came from, so an admin can see at a glance
whether they're reading a partner's idea or a machine's, and can filter the
queue to either.

**The platform itself.** Course topics no longer depend only on who happens to
have registered as a partner. If demand for a skill is rising publicly, the
crawler can notice it.

External developers are unaffected. Nothing about their accounts, limits,
payouts or webhooks changes.

---

## How it works

### The two account kinds

Every developer account now says who stands behind it.

| Source type | Meaning |
|---|---|
| `EXTERNAL` | A third-party developer, whether self-registered or onboarded by an admin. Every account that existed before the crawler is one of these. |
| `SYSTEM` | A platform-owned integration — the crawler. Only one command can create one. |

The label is not cosmetic. It is what the two safety limits below key off, and
those limits bind `SYSTEM` accounts **only**. An external partner never meets
either one.

### Getting the crawler an account

An admin runs a single command, which creates the account, approves it, and
prints its API key once:

```bash
python manage.py provision_mie_system_account \
  --email crawler@soludesks.com \
  --webhook-url https://hooks.example.com/mie \
  --actor-email superadmin@example.com
```

Three things worth knowing:

- **Only a Super Admin can do this.** The command is carried out and recorded
  as the person named by `--actor-email`.
- **The key is shown once**, exactly as with a human partner. There is no way
  to read it again afterwards.
- **It is safe to run twice.** A second run reports the existing account and
  issues no new key, so it can't silently rotate the crawler's credentials. An
  email already used by an external developer is refused outright, so a partner
  account can never be quietly converted into a system one.

The crawler is put on the **account-bypass payout plan**: nothing it submits
ever pays out. It is the platform's own software, so there is no one to pay.

### Submitting an idea

The crawler calls the same endpoint as everyone else, `POST /api/v1/mie/v1/submissions/`,
with its API key in the `X-MIE-Api-Key` header. It may send one extra field:

```json
{
  "title": "Kubernetes Security Hardening for DevOps Engineers",
  "description": "Hardening production clusters: RBAC, admission control, supply-chain checks.",
  "category": "Software Engineering",
  "difficulty_level": "ADVANCED",
  "searches_per_month": 23000,
  "confidence_note": "940 job postings mention this skill (up 34% month on month); 1,200+ related questions on community forums in the past 30 days"
}
```

Everything except the title is optional, but the crawler should send all of
it: those fields are the columns an admin sorts and filters the review queue
by. `category` is matched by name or slug against the platform's real
categories — an unmatched value is not an error, it simply leaves the idea
uncategorised with the raw value kept in the payload.

`confidence_note` is free text, up to 2,000 characters, and it is the crawler's
evidence — the reason it believes this idea is worth building. It is shown to
the admin next to the idea. It is optional and external partners may send it
too, but it exists for the crawler, because a machine's suggestion needs to
show its working in a way a person's doesn't.

Everything else is unchanged: the three deduplication checks run, the idea
lands in the review queue or is short-circuited as a duplicate, and a signed
webhook fires for whatever happened.

---

## The two guardrails

A person naturally paces themselves. Software doesn't, so the platform paces it.

### 1. A daily cap

A `SYSTEM` account may make at most **20 submissions in any rolling 24 hours**
(configurable). Every submission counts, including ones the deduplication
engine rejects immediately — otherwise a crawler stuck resubmitting duplicates
would never hit the limit.

Past the cap, the crawler receives `429 Too Many Requests` with a `Retry-After`
header telling it exactly when its next slot frees up, and **nothing is stored**.

The cap is counted from the database rather than a cache, so it survives a
cache flush and a restart. It cannot be evaded.

### 2. A rejection circuit breaker

A cap limits how *much* the crawler submits. It says nothing about quality — a
crawler producing 20 bad ideas a day is a worse problem than one producing 50
good ones. So the platform watches what admins decide.

Once a `SYSTEM` account has at least **10 admin decisions** inside the last
**7 days**, and **80% or more of them were rejections**, the account is
suspended automatically.

Each threshold is there for a reason. The minimum decision count stops two
unlucky early rejections from reading as a 100% failure rate. The window means
the crawler is judged on recent quality, not on its whole history. Only real
admin decisions count — automatic duplicate detection is not a quality
judgement and is excluded.

When the breaker trips:

- The account is **suspended**, not deleted. Its API key is frozen but kept,
  and its queue history is untouched.
- Every super admin gets an in-app notification and an email saying which
  account was suspended and what its rejection rate was.
- The event is written to the audit trail.
- **An admin reverses it with the ordinary approve action** — the same button
  used for any suspended developer. Webhook events held while it was suspended
  are then delivered rather than lost.
- **Reinstating resets the clock.** The breaker measures from the last decision
  on the account, so a reinstated crawler is judged on what happens next rather
  than immediately tripping again on the rejections that suspended it.

The breaker only ever looks at `SYSTEM` accounts. An external partner with a
poor approval rate is a conversation for a human to have, not something to
automate.

---

## What an admin sees

- **Every submission row carries its `source_type`**, so crawler ideas and
  partner ideas are distinguishable everywhere the queue appears.
- **The queue can be filtered** to one source: `?source_type=SYSTEM` for
  crawler ideas, `?source_type=EXTERNAL` for partner ideas.
- **`confidence_note` appears on the submission**, carrying the crawler's
  evidence for the idea.
- **The recommendations screen shows the source too**, so an admin ranking
  ideas by demand score knows which were machine-suggested.

Reviewing is unchanged. The same approve, reject, scoring and payout-bypass
actions apply, with the same webhooks. An admin need not treat a crawler idea
differently — they just get to know.

---

## Where the crawler itself lives

The parts described above are the platform's side: the account kind, the extra
field, the filter, and the two guardrails. **The crawler program is a separate
service in its own repository, with its own deployment.**

It talks to Course Builder only through the public API, exactly as an external
partner's software would. It does not import the platform's code or touch its
database. Two reasons: a crash or a bad deploy on the crawler cannot affect the
platform, and the heavy dependencies a crawler needs — for fetching and
scoring web signals — stay out of the platform's container image.

The crawler is also responsible for the things only it can do: respecting each
data source's terms of service, clustering similar signals so it doesn't submit
five wordings of the same idea, and budgeting its own submissions so it rarely
meets the platform's cap at all. The platform's limits are the backstop, not
the plan.

---

## What this is worth

**Demand-led topics.** The catalogue can follow what the market is asking for,
rather than only what partners happen to propose.

**No new review burden.** Crawler ideas enter the existing queue, deduplicated
and labelled, and can be filtered out entirely by an admin who wants to review
partners only.

**Safe by construction.** A runaway crawler is bounded by a cap it cannot
evade, and a low-quality one switches itself off and tells a human why.

**No risk to the existing pipeline.** External partners, their payouts and
their webhooks are untouched, and the crawler runs outside the platform
entirely.
