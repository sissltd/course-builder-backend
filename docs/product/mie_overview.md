# MIE (Market Intelligence Engine)

## What is MIE?

MIE is a system that lets external course creators submit course ideas to Course Builder. Instead of building courses themselves, these creators act as "idea generators" -- they research what courses people want, submit their ideas, and get paid when their ideas are accepted and turned into real courses.

Think of it like a suggestion box with a payment attached: creators suggest what courses to build, the platform decides which ones are worth building, and the creators get credited for their contributions.

---

## Who is it for?

**External Developers / Creators**
People outside the platform who want to contribute course ideas and earn money. They register, get approved, and start submitting ideas through a simple API.

**Platform Administrators**
The team that reviews submitted ideas, decides which ones to pursue, and manages the pipeline. Admins have a full dashboard to filter, score, approve, or reject submissions.

---

## How it works (the journey)

### Step 1 -- Registration

A creator signs up with their email and a webhook URL (the address where they will receive notifications about their ideas). Their account starts as "Pending" until a platform admin approves it. At this stage, the creator cannot do anything except wait.

### Step 2 -- Approval

A superadmin reviews the registration and either approves or rejects it. On approval, the creator receives an API key -- a unique password their software uses to talk to the platform. For security, this key is shown exactly once at the moment of approval. The creator must copy it immediately; it cannot be retrieved later.

### Step 3 -- Submitting Ideas

The creator submits course ideas. Each idea is automatically checked against three things before it ever reaches a human reviewer:

- **Was this idea rejected before?** -- "We've seen this before, and the answer was no."
- **Does a course with this title already exist?** -- "We already have this."
- **Is someone else submitting the same idea right now?** -- "Someone else just submitted this."

If none of the above applies, the idea goes into the review queue for a human to evaluate. The creator always gets an immediate response telling them which outcome occurred.

### Step 4 -- Admin Review

Platform admins see all submissions in a dashboard. They can:

- **Approve** ideas -- the creator gets paid according to their plan
- **Reject** ideas -- with a reason and optional note, and the creator is notified
- **Set recommendation scores** -- a demand score (0--100) and estimated monthly earnings to help prioritize which ideas to pursue first
- **Toggle payout settings** per individual idea if needed

Decisions are reversible. An approved idea can later be rejected, and vice versa. Every flip sends a notification to the creator.

### Step 5 -- Notifications

Every decision -- approvals, rejections, duplicates, and payout changes -- is immediately sent to the creator's webhook URL. The creator never has to guess or check a dashboard; they always know the status of their ideas in real time.

---

## Key Concepts

### Deduplication

The system automatically catches duplicate ideas before they reach the admin queue. This saves reviewers from evaluating the same idea twice and keeps the pipeline clean. There are three types of duplicates: ideas that were rejected before, ideas that match an existing course, and ideas that someone else already submitted and are still awaiting review.

### Payout Plans

Different creators can have different payment arrangements. Some get paid for every approved idea. Others get paid unless a specific idea is marked as excluded. Others never receive payouts (for example, when the creator is contributing for non-commercial reasons). Admins set the plan per creator.

### Rejection Reasons

A shared vocabulary of rejection reasons (managed by admins) ensures that feedback is consistent across all reviewers. Every rejection comes with a required reason from this list, plus an optional free-text note for extra context.

### Webhooks

Webhooks are real-time notifications. Instead of the creator checking the platform to see what happened to their idea, the platform pushes a message to the creator's server the instant something changes. Every state change -- from initial submission through approval or rejection -- triggers a webhook.

### Reference Numbers

Every idea gets a tracking number in the format `SCB-xxxxxxxx-S` (for example, `SCB-0d1c7b2e-A`). The last letter tells you the status at a glance:

| Letter | Status |
|--------|--------|
| P | Pending Review |
| D | Duplicate In Queue |
| E | Duplicate Existing (course already exists) |
| X | Previously Rejected |
| A | Approved |
| R | Rejected |

The letter changes as the idea moves through the system, so you can always see where it stands.

---

## What makes it secure?

- **API keys are one-time-viewable.** The key is shown once at approval and never again. If lost, it must be rotated by an admin.
- **Each creator can only see their own submissions.** There is no way for one creator to browse or access another creator's ideas or account details.
- **All actions are logged and traceable.** Every approval, rejection, and webhook delivery is recorded with timestamps.
- **Rejected accounts lose all access immediately.** A rejected or suspended account cannot authenticate or submit anything. Suspension is reversible; rejection requires a new registration.

---

## Admin Capabilities

Platform administrators have a full-featured dashboard for managing the pipeline:

- View all submissions across all creators in one place
- Filter by status, creator, date range, or search by title / email
- Approve or reject ideas with immediate creator notifications
- Set demand scores and estimated earnings to prioritize which ideas to pursue
- Manage the rejection-reason taxonomy (add, edit, or deactivate reasons)
- Toggle payout bypass on individual submissions
- View webhook delivery history for debugging failed notifications

---

## What is the business value?

**Crowdsourced course ideation.** Let the market tell you what courses to build. External creators are incentivized to research and submit high-demand topics.

**Quality pipeline.** Automatic deduplication plus human review means only unique, vetted ideas reach the production stage.

**Creator engagement.** Real-time feedback through webhooks keeps contributors active and informed. They always know where they stand, which encourages continued participation.

**Scalable.** The system handles one creator or a thousand with the same efficiency. The admin dashboard, automated dedup, and webhook notifications all scale without additional manual effort.
