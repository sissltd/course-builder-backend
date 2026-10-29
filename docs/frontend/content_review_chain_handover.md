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

---

## Part 3 — The staged review flow (behind a platform setting)

Everything above describes the original flow, which is still what runs. A
second flow — **text first, video second** — ships switched **off** behind
`staged_review_flow_enabled` on `GET /api/v1/platform-settings/`. Read that
field and branch on it; with it `false` nothing in this Part applies. An Admin
turns it on with `PATCH /api/v1/platform-settings/ {"staged_review_flow_enabled": true}`,
which returns `409` (`staged_review_flow_in_flight`) while any course is in
review, awaiting video or revision, or approved but unpublished — show the
message and ask them to wait for the queue to drain.

### The flow

```
DRAFT ─submit (text only)─▶ First Review ─▶ AWAITING_VIDEO ─submit-video─▶ Second Review ─▶ Verification ─▶ QA ─▶ APPROVED ─▶ PUBLISHED
                            (a Writer          (video added)               (a different       (a Verifier)             (the Approver
                             reads the text)                                Writer watches                               prices + publishes)
                                                                            the video)
       any seat rejects ─▶ NEEDS_REVISION ─fix, resubmit─▶ back at that same seat only
```

| Seat | `review_stage` | Who can take it (staged flow) |
|---|---|---|
| First Review (text) | `CONTENT` | Writer |
| Second Review (video) | `SECOND_REVIEW` | Writer — **not** the one who did First Review |
| Verification | `VERIFICATION` | Verifier |

### New statuses and fields

- `AWAITING_VIDEO` — the text passed First Review and the course is waiting for
  its video. `review_stage` is empty.
- `NEEDS_REVISION` — a seat sent the course back. The creator edits it exactly
  as a `DRAFT` (every builder edit endpoint now accepts both). `revision_seat`
  says which seat will review it next.
- On the course detail: `review_stage`, `revision_seat`, `video_provider`
  (`CREATOR`, `PRODUCTION_ENGINE`, `DEVELOPER`, or empty until the creator
  chooses) and `video_attached_at`.
- The creator dashboard counts and `/admin/courses/` filters include the two
  new statuses with no change on your side.

### What the creator does

1. **Submit text only.** `POST /courses/{id}/submit/` now returns `400`
   (`structural_standards`) if the course carries *any* video — preview video,
   a lesson `video_url`/`embedded_link`, a video content block or a registered
   video asset. A `VIDEO`-type lesson may be saved with only a script.
2. **After First Review** the creator gets a notification with
   `metadata.action = "video_decision"`. Open a prompt: "Add your video, or
   leave it to video production?" and call
   `POST /courses/{id}/video-decision/ {"will_provide": true|false}`.
   It can be changed until the video is submitted. `409` if the course is not
   `AWAITING_VIDEO` or is a developer's course.
3. **Add the preview video and each video lesson's media** with the existing
   edit endpoints, then `POST /courses/{id}/submit-video/` (no body). `400`
   `structural_standards` lists what is missing; `409` if it is not the
   creator's to supply (they chose production, or the course isn't waiting).
4. **If a seat sends the course back**, show the reviewer's feedback and let
   the creator edit and press the usual submit — `POST /courses/{id}/submit/`
   works from `NEEDS_REVISION` and resumes at `revision_seat`. An appeal
   (`/course-appeals/`) works from `NEEDS_REVISION` too.

If the creator chooses video production, the course stays `AWAITING_VIDEO`
until production delivers it. **Production is not built yet**, so show a
"video is being prepared" state rather than an error.

### What reviewers see

- Claim → approve/reject work as before; only who may take each seat changed
  (Writers instead of Creator Reviewers on the first two).
- Approving First Review no longer moves the course to Second Review: it leaves
  the queue as `AWAITING_VIDEO` and re-enters at Second Review when the video
  arrives. A rejection now shows `NEEDS_REVISION` rather than `DRAFT`.
- **Pricing and publishing are Approver-only.** `PUT .../review-prices/` and
  `POST .../publish/` return `403` ("Only the Approver can price and publish a
  course.") for anyone else, Admins included. Hide those actions unless the
  user is an Approver or Super Admin. Publishing is also the "trigger final
  production" step.
- The seat labels are unchanged in the API (`First Review`, `Second Review`);
  label them "Text review" and "Video review" yourself when the setting is on.

### Who can see a course (staged flow only)

A course is visible **only to the role sitting its current seat, and to the
Super Admin**; it appears for the next role only once it leaves the previous
one. Everything else returns `404` (not `403`), on lists, screens, detail and
every action (claim, approve, reject, QA, prices, publish):

| Course is at | Visible to |
|---|---|
| First Review / video review (`SUBMITTED`/`IN_REVIEW`, `CONTENT`/`SECOND_REVIEW`) | Writers |
| Verification | Verifiers |
| `QA_VERIFICATION` | QA Reviewers |
| `APPROVED` | Approver |
| `NEEDS_REVISION` | the roles of the seat that sent it back (`revision_seat`) |
| `PUBLISHED` | everyone with course access |
| `DRAFT`, `AWAITING_VIDEO` | no reviewer role |

So the **Admin and Creator Reviewer no longer see in-flight courses**, and the
Admin cannot assign a seat on a course at another role's seat. The same filter
applies to `/admin/courses/`, `/courses/` for staff who view every course,
global search and the reviewer dashboard counts. Don't offer a link to a course
the user can't open; expect a `404` if one is stale.

### Creating a course (staged flow only)

`topic` is now **required** on `POST /courses/`, `POST /course-ai-generations/`
and `POST /course-imports/`: `400` with `field_name: "topic"` when missing. A
creator picks an existing topic or requests one (`POST /topic-reservations/`)
and starts the course once it is approved. A creator is now **notified when
their topic request is approved or declined** (with the reviewer's reason) —
this part applies whatever the setting says. A topic that is not active is
refused (`400`) whatever the setting says.

### Summary of client-visible changes

| Change | Applies |
|---|---|
| `staged_review_flow_enabled` on platform settings; `409` when changing it mid-flight | always (additive) |
| `AWAITING_VIDEO`, `NEEDS_REVISION` statuses; `review_stage`, `revision_seat`, `video_provider`, `video_attached_at` on course detail | always (additive) |
| `POST /courses/{id}/video-decision/`, `POST /courses/{id}/submit-video/` | new; `409` unless staged |
| Submit refuses any video; rejection → `NEEDS_REVISION`; resubmit resumes at the seat | staged flow only |
| Writers sit First/Second Review; Approver-only pricing and publish; a course is visible only to the role at its seat (`404` otherwise) | staged flow only |
| `topic` required for creators (manual, AI, import) | staged flow only |
| Builder edit endpoints accept `NEEDS_REVISION`; their `400` text now reads "Draft or Needs Revision" | always |
| Topic-request approve/decline notifies the requester; inactive topic refused (`400`) | always |
