# MIE Course Delivery — Frontend Handover

What changed on the MIE side across three backend changes:
- PR #100: video assignment, archive, and the course id at approval
- PR #102: the Studio review endpoints, built by Benneth
- this PR: each developer's journey stage, plus the Studio docs and Swagger **No breaking
changes.** Everything you already built keeps working. To build:
- three new admin actions
- a journey stage on every developer account, for both admins and the
  developer, plus a filter
- a few new read-only fields

The Studio work needs no admin screens. Read [mie_frontend_guide.md](mie_frontend_guide.md)
first if you haven't built the MIE screens yet; this only covers what is new.

---

## Part 1 — The logic

### The journey, end to end

An idea and its course are two separate things with two separate statuses.
The idea is approved once; the course then moves through review on its own.

```
1. Developer registers                      → account PENDING
2. Super Admin approves the developer       → API key shown once
3. Developer submits an idea                → dedup, then PENDING_REVIEW
4. Super Admin approves the idea            → idea APPROVED, course_id reserved  (NEW)
5. Developer delivers the course text       → course SUBMITTED → review
      direct:  POST /mie/v1/submissions/{id}/course/
      Studio:  POST /mie/v1/studio/reviews/   kind "text"                         (NEW)
6. Text passes the first review seat        → course AWAITING_VIDEO
7. Who makes the video?                                                           (NEW)
      default:                         the developer
      account set to "assign videos":  waits until a Super Admin picks
                                       PRODUCTION_ENGINE or DEVELOPER
8. Video arrives                            → course continues through review
      developer, direct:  POST /mie/v1/submissions/{id}/course/video/
      developer, Studio:  POST /mie/v1/studio/uploads/, upload,
                          then POST /mie/v1/studio/reviews/  kind "video"         (NEW)
      production engine:  made in-house
9. QA verification → payout → PUBLISHED
   At any point before QA passes, a Super Admin can archive the course.          (NEW)
```

A rejection in review sends the course back to the developer to fix and send
again. This is not new.

### The course id is known at approval (new)

Until now a course only got its id when the developer first pushed it. Now the
id is **reserved the moment the idea is approved**, and the course is created
under that same id later. For you this means:

- `course_id` exists from approval onwards and never changes.
- Before the course is pushed, the id points at nothing. **Don't link to the
  course page until the course exists.** The admin submission row's
  `resulting_course` is still null until the first push, as before. Use that
  to decide whether to show the link.

### Video assignment (new)

Some developers (production studios, for example) should only make a video
when the platform asks. A Super Admin can switch a developer account to
**requires video assignment**:

| Setting | What happens when a course's text passes the first seat |
|---|---|
| `false` (default) | The course is `AWAITING_VIDEO` and the developer sends the video. Nothing for an admin to do. |
| `true` | The course is `AWAITING_VIDEO` with **no video producer**. It waits until a Super Admin assigns one. |

Assigning means picking one of two producers:

- `PRODUCTION_ENGINE`: the platform makes the video. A production run is
  queued immediately.
- `DEVELOPER`: the developer sends it. Any production run in progress is
  cancelled.

An admin can also use this on a course whose developer **doesn't** require
assignment, to take the video in-house instead. The assignment can be changed
any number of times **until the video is submitted**. The developer is told
each time by a `COURSE_VIDEO_ASSIGNED` webhook.

The setting only applies to texts approved after it is changed. A course
already waiting keeps the producer it has.

### Archiving (new)

Archive is a **final no** for a developer's course. It is different from a
reviewer rejection, which only sends the course back for another revision.

- **Allowed:** any time before the course passes QA or is published.
- **Reason:** required. It's free text, up to 1,000 characters, written for
  the developer, because it is sent to them word for word.
- **Effects:**
  - The course leaves every review queue.
  - Any production run on it is cancelled.
  - The developer can never push it again (`409 course_archived`).
- **What stays:** a payout already made is not reversed, and the idea keeps
  its `APPROVED` status.
- **It can't be undone.** There is no unarchive.

### Each developer's journey stage (new)

Every developer account now carries a `journey`: how far it has got along
the journey above. Nobody sets it. The backend works it out on every read
from the account, its ideas and their courses, so it is always current.

| Step | `stage` | Means |
|---|---|---|
| 1 | `REGISTERED` | Not approved yet. |
| 2 | `ONBOARDED` | Approved, no idea sent yet. |
| 3 | `IDEA_SUBMITTED` | Has sent at least one idea. |
| 4 | `IDEA_APPROVED` | Has an approved idea, no course delivered yet. |
| 5 | `COURSE_DELIVERED` | Has delivered a course; it's in content review (or was archived). |
| 6 | `TEXT_APPROVED` | A course passed content review: waiting for video, or in QA. |
| 7 | `QA_PASSED` | A course passed QA, which is the payout point. |
| 8 | `PUBLISHED` | A course is live. |

Two rules to keep in mind when you display it:

- **It's the furthest point reached.** A developer with one published course
  and three ideas still in review is `PUBLISHED`. The counts in the block
  give the rest of the picture.
- **It can move back.** If the only course that got a developer to
  `TEXT_APPROVED` is sent back for revision, they return to
  `COURSE_DELIVERED`. Don't animate it as a one-way progress bar or cache
  it.

The journey is separate from the account `status`. A `SUSPENDED` developer
keeps their stage. Show both.

### Soludesk Studio (new, nothing to build)

Studio is an AI course generator that acts as an ordinary MIE developer
account with its own API key. It has its own two endpoints, added in PR
#102: one for sending course text and video for review, and one for
uploading its video files. Behind them sits the same push code every
developer uses. Two things are specific to Studio:
- It only works while the platform reviews text before video.
- Its text arrives with every lesson as TEXT. Its video review then turns
  each lesson into a video lesson, carrying the video, captions and
  transcript.

For the frontend, that means:

- A Studio course is an ordinary course. It shows `source_type = DEVELOPER_API`
  like any other developer course and goes through the same review seats,
  webhooks and payouts.
- Its idea, approval, video assignment and archive all use the screens in this
  document. **There is no Studio-specific admin screen and no new admin
  field.**
- The Studio endpoints take the API key only. They are not for the frontend to
  call.

---

## Part 2 — What you need to do

All `/mie/admin/…` routes below take a Super Admin's platform JWT
(`Authorization: Bearer …`), as before. API keys are rejected there.

### 1. Video assignment switch on the developer

`GET /api/v1/mie/admin/developers/` and `.../{id}/` now return:

| Field | Type | Notes |
|---|---|---|
| `requires_video_assignment` | boolean | `false` by default. |

Show it on the developer detail as a toggle labelled something like
"Assign videos per course". To change it:

```
POST /api/v1/mie/admin/developers/{id}/video-assignment/
{ "requires_video_assignment": true }
```

- `200` returns the whole developer account (same shape as the detail), so
  replace your cached row with it.
- Setting the value it already has is fine: `200`, nothing changes.
- **Hide the toggle for the crawler** (`source_type = "SYSTEM"`). The
  backend refuses it there. The crawler never makes videos.
- No webhook fires. The developer sees the value on their own
  `GET /api/v1/mie/v1/me/` (`requires_video_assignment`), so show it read-only
  on the developer's profile page if you render one.

### 2. "Assign video" action on a developer's course

Shown on the submission detail when the idea has a course
(`resulting_course` is set) and that course is `AWAITING_VIDEO`. Load the
course with the course detail you already use: it carries `status` and
`video_provider` (null while unassigned).

```
POST /api/v1/mie/admin/submissions/{submission_id}/course/video-assignment/
{ "video_producer": "PRODUCTION_ENGINE" }      or  "DEVELOPER"
```

`{submission_id}` is the **idea's** id, not the course id.

`200` response:

```json
{
  "submission_id": "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11",
  "submission_reference": "SCB-0d1c7b2e-A",
  "course_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "title": "Build a Production-Grade Rust Course",
  "status": "AWAITING_VIDEO",
  "video_producer": "PRODUCTION_ENGINE"
}
```

UI guidance:

- Two choices: **"Platform makes it"** (`PRODUCTION_ENGINE`) and **"Developer
  sends it"** (`DEVELOPER`). Pre-select the current `video_provider` and
  disable that option: picking the producer it already has is a `400`.
- If `video_provider` is null on an `AWAITING_VIDEO` course, the course is
  **stuck until someone assigns it**. Show that clearly, as a badge or an
  "Action needed" state in the queue.
- Warn before switching away from `PRODUCTION_ENGINE`, because a run in
  progress is cancelled.
- For a crawler (`SYSTEM`) idea, only offer `PRODUCTION_ENGINE`.

Errors:

| Status | Code | Show |
|---|---|---|
| `400` | validation | Same producer as now, or a bad value. |
| `404` | `not_found` | No such idea, or no course pushed yet. |
| `409` | `course_not_awaiting_video` | "This course isn't waiting for its video any more." Refresh the course. |
| `409` | `video_provider_conflict` | The crawler's course can't go to its developer. |

### 3. "Archive course" action

Shown on the submission detail when the idea has a course whose `status` is
one of `DRAFT`, `SUBMITTED`, `IN_REVIEW`, `NEEDS_REVISION`, `AWAITING_VIDEO`
or `QA_VERIFICATION`. Hide it for `APPROVED` (passed QA, already paid),
`PUBLISHED` and `ARCHIVED`.

```
POST /api/v1/mie/admin/submissions/{submission_id}/course/archive/
{ "reason": "The course repeats our existing Rust track and two revisions did not change its scope." }
```

- `200` returns the same course shape as above with `"status": "ARCHIVED"`.
- **Always confirm first.** It's irreversible. Use a dialog with a required
  reason box (max 1,000 characters, with a counter) and wording like "The
  developer will see this reason. This can't be undone."
- After archiving, hide both actions and show an "Archived" badge on the
  course.

Errors:

| Status | Code | Show |
|---|---|---|
| `400` | validation | Reason missing or too long. |
| `404` | `not_found` | No such idea, or no course pushed yet. |
| `409` | `course_not_archivable` | Already archived, or past QA / published. Refresh. |

### 4. Show `course_id` on the developer's own submissions

`GET /api/v1/mie/v1/submissions/queue/` rows (developer side, API key or
platform session) now carry:

| Field | Type | Notes |
|---|---|---|
| `category_id` | uuid \| null | The idea's category. |
| `course_id` | uuid \| null | Null until the idea is first approved, then fixed for life. |

If you render a developer-facing queue, show `course_id` as a copyable id on
approved ideas. It's what the developer's own system keys on. Don't link it to
a course page until the course exists (see Part 1).

### 5. New webhook event types appear automatically

The developer's webhook settings list their event types from
`GET /api/v1/mie/v1/webhooks/event-types/`. These are now in that list:

| Event | When |
|---|---|
| `COURSE_VIDEO_ASSIGNED` | An admin assigned the video (carries `video_producer`). |
| `COURSE_REJECTED` | A reviewer rejected the course; followed by `COURSE_REVISION_REQUESTED`. |
| `COURSE_ARCHIVED` | An admin archived the course (carries `archive_reason`). |

If your event picker is driven by that endpoint, there's nothing to do. If you
hard-coded the list anywhere, switch it to the endpoint.

### 6. The developer docs page has a new section

`GET /api/v1/mie/v1/documentation/` is now version `3.3.0`, with a new
top-level `studio` section (the Studio review contract) and two new entries
in the endpoint list. If your docs page renders known sections by key, add
`studio`. Title it **"Soludesk Studio Deliveries"**, the same as the PDF. If it
renders whatever keys come back, the only thing to check is the rendering
below.

What's inside `studio`, and how to render it:

| Key | Shape | Render as |
|---|---|---|
| `summary` | string | Intro paragraph. |
| `flow` | list of strings | Numbered steps. |
| `requirements` | list of strings | Bullets, as a callout. |
| `envelope` | object, field → description (includes the `Idempotency-Key (header)` row) | Two-column table. |
| `content_structure.summary` | string | Paragraph. |
| `content_structure.text`, `.video` | lists of strings, indented with leading spaces | **Monospace block, keeping the spaces.** The indentation is the nesting (course > modules > lessons > blocks). |
| `content_structure.list_items`, `.text_carries_no_video`, `.field_rules` | strings | Paragraphs. |
| `examples.text`, `examples.video` | whole JSON reviews (the text one is large) | Pretty-printed JSON code blocks, collapsible, with a copy button. |
| `examples.note` | string | Caption under the examples. |
| `receipt` | string | Paragraph. |
| `uploads` | list of strings | Bullets. |
| `receipts` | list of strings | Bullets. |

Other docs changes that reach this page:

- The endpoint entries **"Studio: reserve or resume a media upload"** and
  **"Studio: deliver course text or video for review"** have a
  `request_example` (the review's is the same large text review) and, for
  the review, a `response_example`. Render them the way you already render
  other endpoints' examples.
- `your_account` gains `journey`, `journey_stages` and `journey_rule` (see 8).
- `rate_limits.limits` gains two rows, one per Studio endpoint. Uploads now
  have their own limit, apart from reviews. Render them like the others.
- `course_schema.content_block.text_content` now defines the list format:
  `NUMBERED_LIST` and `BULLETED_LIST` hold **one item per line** (`\n`), with
  no blank lines and no bullet or number typed in (`- `, `* `, `1. `). Studio
  text reviews refuse anything else. **Please check the course editor** saves
  TipTap `orderedList` / `bulletList` this way, so editor courses and Studio
  courses store lists alike. The editor's own save isn't checked, so nothing
  breaks if it differs, but lists would render differently.
- `course_schema.question.explanation` now says it is **required for ESSAY**
  questions. The backend has always enforced this; only the docs were wrong.
  If the course editor lets a creator save an essay question with no
  explanation, the save fails with `400 explanation`. Worth checking that the
  editor marks the field as required for essays.

Swagger also changed: the Studio review body is now two typed schemas,
picked by `kind`. `StudioReviewRequest` is either
`StudioTextReviewSchemaRequest` or `StudioVideoReviewSchemaRequest`, and the
`Idempotency-Key` header is documented as required. The frontend never calls
this endpoint. This only matters if you generate a client from the whole
schema. The upload `purpose` enums keep the names they had before #102
(`UploadRequestPurposeEnum`, `MieUploadRequestPurposeEnum`); staging briefly
called the first one `PurposeFedEnum`.

### 7. Show the journey stage, admin side

`GET /api/v1/mie/admin/developers/`, `.../{id}/` and every admin action
that returns an account (approve, video-assignment toggle) now include:

```json
"journey": {
  "stage": "COURSE_DELIVERED",
  "label": "Course Delivered",
  "step": 5,
  "total_steps": 8,
  "next_step": "Your course is in content review. On COURSE_REVISION_REQUESTED, fix it and deliver it again; otherwise wait for COURSE_TEXT_APPROVED.",
  "ideas_submitted": 3,
  "ideas_approved": 1,
  "courses_delivered": 1,
  "courses_published": 0
}
```

- **Developers table:**
  - Add a "Stage" column showing `label`, with `step`/`total_steps` as a
    small progress indicator.
  - Add a stage filter:

    ```
    GET /api/v1/mie/admin/developers/?journey_stage=IDEA_APPROVED
    ```

    It combines with `?status=` and `?plan_type=`. **An unknown value is a
    `400`**, so build the dropdown from the eight values in Part 1, in that
    order.
- **Developer detail:** a stepper with the eight stages and the current one
  highlighted, plus the four counts. `next_step` is written to the developer
  ("Your course…"). On the admin side, show it as "Developer's next step".
- **Useful saved filters** for admins chasing stalled developers:
    - `ONBOARDED`: approved but never sent an idea
    - `IDEA_APPROVED`: approved idea, course not delivered

### 8. Show the journey stage, developer side

`GET /api/v1/mie/v1/me/` returns the same `journey` block for the developer
themself. Show it at the top of the developer's profile or dashboard:
- the stepper
- `next_step`, worded for them already, as the call to action
- the counts

The developer docs (`GET /api/v1/mie/v1/documentation/`) also carry it under
`your_account`:
- `journey`: the live block
- `journey_stages`: all eight stages in order, with `next_step` for each
- `journey_rule`: the "furthest point, can move back" rule in words

A `PENDING` developer can't reach `/me` (`401`), so the developer side never
shows `REGISTERED`.

### 9. Nothing to build for Studio

The two Studio endpoints (`/mie/v1/studio/reviews/`, `/mie/v1/studio/uploads/`)
are machine-to-machine and take the API key only. Studio courses appear in the
existing admin and reviewer screens like every other developer course, and use
actions 2 and 3 above like any other.

---

## Summary

| Surface | Change | Breaking? |
|---|---|---|
| Admin developers list + detail | `requires_video_assignment` added | No — additive |
| Admin developer | `POST …/developers/{id}/video-assignment/` toggle | New action |
| Admin submission detail | `POST …/submissions/{id}/course/video-assignment/` | New action |
| Admin submission detail | `POST …/submissions/{id}/course/archive/` (irreversible, reason required) | New action |
| Developer `/me` | `requires_video_assignment` added (read-only) | No — additive |
| Developer queue rows | `category_id`, `course_id` added | No — additive |
| Webhook event types | `COURSE_VIDEO_ASSIGNED`, `COURSE_REJECTED`, `COURSE_ARCHIVED` | No — served by the endpoint |
| Developer docs | `studio` section (with `content_structure` and full `examples`), version 3.3.0 | No — additive |
| Developer docs `course_schema` | essay `explanation` documented as required (already enforced) | No — docs fix |
| Admin developers list + detail | `journey` block added; `?journey_stage=` filter (invalid → `400`) | No — additive |
| Developer `/me` and docs | `journey` block (docs: `journey_stages`, `journey_rule`) | No — additive |
| Studio endpoints | API key only, no UI | — |

The three admin actions need the `mie.manage_console` permission, which Super
Admins have by default.
