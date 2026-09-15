# Content Review Chain — Frontend Handover

Content review used to be one decision. It is now **three sequential seats,
each held by a different person**, before the existing QA and publish steps.
No new endpoints — the same claim, approve and reject routes are now
seat-aware. But this **does change behaviour you already built**, so read
Part 1 before touching the reviewer screens.

---

## Part 1 — The logic

### The flow

```
DRAFT ─submit─▶ First Review ─▶ Second Review ─▶ Verification ─▶ QA ─▶ APPROVED ─▶ PUBLISHED
                (Creator         (a different     (a Verifier)
                 Reviewer)        Creator Reviewer)
                     │                 │                │
                     └──── any rejection sends the course back to DRAFT ────┘
```

| Seat | `review_stage` | Who can take it |
|---|---|---|
| First Review | `CONTENT` | Creator Reviewer |
| Second Review | `SECOND_REVIEW` | Creator Reviewer — **not** the one who did First Review |
| Verification | `VERIFICATION` | Verifier |

Admin, Staff Approver and Super Admin (the "Admin tier") may take any seat.

### How a seat moves

Each seat runs the same two steps — **claim, then decide**:

| Moment | `status` | `review_stage` |
|---|---|---|
| Waiting for someone to claim the seat | `SUBMITTED` | the seat |
| Someone has claimed it | `IN_REVIEW` | the seat |
| First Review approves | `SUBMITTED` | `SECOND_REVIEW` |
| Second Review approves | `SUBMITTED` | `VERIFICATION` |
| Verification approves | `QA_VERIFICATION` | `QA` |
| Any seat rejects | `DRAFT` | — |

So a course goes back to `SUBMITTED` between seats. `status` alone no longer
tells you where a course is in content review — **`review_stage` does**.

### The rules

1. **Claim before deciding.** A reviewer must claim a seat before approving or
   rejecting it. Approving an unclaimed course returns `400`.
2. **Only the claimant decides.** A reviewer who didn't claim the seat gets
   `403`.
3. **Four eyes.** Whoever decided an earlier seat on this course can't take a
   later one in the same cycle — this applies to admins too.
4. **Role per seat.** A Creator Reviewer can't verify; a Verifier can't do
   First or Second Review.
5. **Admin override.** The Admin tier may decide a seat someone else claimed,
   or one nobody claimed. The seat moves to them and it is logged.
6. **Rejection restarts everything.** Any seat's rejection returns the course
   to `DRAFT`. When the creator resubmits, it starts again at First Review with
   every seat empty. An approved appeal restarts it the same way.
7. **One decision at a time.** If two people decide the same seat at the same
   moment, the second gets `400` and must reload.

### What the creator sees

The creator is notified **once**, when Verification approves ("Course passed
content review"), and on any rejection. The two intermediate approvals send
the creator nothing.

---

## Part 2 — What you need to do

### 1. Read `review_stage`, not just `status`

`review_stage` is on the reviewer course **list and detail**. Use it to label
where a course is — "First Review", "Second Review", "Verification". It is only
meaningful while `status` is `SUBMITTED` or `IN_REVIEW`, and reads `QA` once the
course is in `QA_VERIFICATION`. Ignore it for any other status.

### 2. Always claim before approve or reject

`POST /api/v1/review-queue/{id}/claim/` must come before
`POST /api/v1/review-queue/{id}/approve/` or `.../reject/`. If today your
Approve button calls approve directly on a `SUBMITTED` course, it will now fail
with `400`. Either claim when the reviewer opens the course, or chain claim →
approve on the button.

The `content-approve` and `content-reject` aliases behave identically.

### 3. Stop expecting one approval to reach QA

After an approve, the course usually comes back as `SUBMITTED` at the next
seat — not `QA_VERIFICATION`. Only the Verification approval moves it to QA.
Anything that assumes "approved → QA queue" needs to read `review_stage`.

### 4. Expect a smaller Pending queue

`GET /api/v1/review-queue/pending/` now lists only the seats the caller can
actually take:

| Caller | Sees |
|---|---|
| Creator Reviewer | First and Second Review seats — minus courses where they decided an earlier seat |
| Verifier | Verification seats only |
| Admin tier | Every seat |

So a reviewer who just approved First Review will **not** see that course in
Pending afterwards. That's correct, not a bug. The in-review, approved and
published screens are unchanged.

### 5. Handle the new refusals

| Status | Message | What to show |
|---|---|---|
| `400` | `Claim this course first.` | Claim it, or prompt the reviewer to |
| `400` | `This course is already assigned to another reviewer.` | Someone else holds the seat |
| `400` | `This course moved on while you were reviewing it. Reload it and try again.` | Refresh the course |
| `403` | `The <seat> seat is for a <role>.` | This role can't take this seat |
| `403` | `You decided an earlier seat on this course, so a different reviewer must take the <seat> seat.` | Four eyes — hide the action for them |
| `403` | `Only the reviewer who claimed this course can decide it.` | Not the claimant |

Show the message text as-is; it's written for the reviewer.

### 6. Filter by seat on the admin course list

```
GET /api/v1/admin/courses/?review_stage=SECOND_REVIEW
```

Accepts `CONTENT`, `SECOND_REVIEW`, `VERIFICATION` (courses at that seat right
now) and `QA` (courses in QA verification). Note this filter is on
`/admin/courses/`, not on `/review-queue/`.

### 7. Staff Verifiers lose two seats

Staff Verifiers could previously do content review. Now they can only take
Verification. Any UI that offers them First or Second Review should hide it —
the server refuses with `403`.

---

## Summary

| Change | Breaking? |
|---|---|
| Must claim before approve/reject | **Yes** — `400` if skipped |
| One approval no longer reaches QA; three do | **Yes** — behavioural |
| Staff Verifiers limited to Verification | **Yes** — `403` on the first two seats |
| Pending scoped to the caller's claimable seats | Behavioural — fewer rows |
| `review_stage` on reviewer list and detail | No — additive |
| `?review_stage=` accepts the new seats on `/admin/courses/` | No — additive |
| Creator notified once, after Verification | Behavioural |

Courses already in review when this shipped were moved to First Review. Any
claim held by a Staff Verifier at that moment was released back to Pending.
