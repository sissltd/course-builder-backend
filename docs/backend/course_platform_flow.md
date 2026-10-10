# Course Platform — Backend Flow

This doc walks through how the "Create a Course" wizard, quiz builder, dashboard, and collaborators screens map onto the Django app. Read it as if we're pairing on the implementation — I'll call out the model each screen writes to, the fields it owns, and the gotchas I'd flag in a PR review.

Companion files: `course_platform_schema.sql` (raw DDL) and `course_platform_schema.json` (structured schema). This doc is the "why" and "in what order" layer on top of those.

---

## 1. App layout

Before touching the wizard, here's how I'd split this into Django apps so nothing gets tangled:

```
courses/        # Course, Module, Lesson, content blocks, thumbnails, tags, versions
quizzes/        # Quiz, Question, QuestionOption — kept separate because it's reused
                #   from both the lesson editor and (later) student-facing attempts
collaborators/  # CourseCollaborator + WorkspaceCollaborator
reviews/        # ReviewAction, ReviewAssignment, ReviewFlag, QualityCheckRun, QualityFinding, MediaAsset
catalog/        # Category, Topic, CategoryRequest — shared lookup data, admin-managed
```

`quizzes` and `reviews` being their own apps matters once you start writing serializers — a `Lesson` shouldn't need to import from `reviews` just to render a dashboard badge, and student-facing quiz-taking (later) will want `quizzes` decoupled from the authoring side entirely.

---

## 2. The big picture

The whole "Create a Course" experience is **one Course object being built up across an ordered set of steps**, not a chain of independent forms. Every step reads/writes the same `course_id` (carried in the URL or session), and every step (after the first) shows a persistent sidebar with checkmarks for what's done — so from a backend point of view this is a **stateful multi-step form**, not a wizard library gimmick. I'd model it as one `Course` row created eagerly at step 1, then `PATCH`ed at every subsequent step. Don't wait until the end to create the row — the "Saved 2 mins ago" indicator in the top bar tells you it's autosaving per field, so every step needs its own lightweight update endpoint.

```
Legal agreements
      ↓
Select course category → Select course topic → Enter course title
      ↓
Course Information  (difficulty, tags, learning objectives, overview)
      ↓
Course Outline      (module titles only — lightweight skeleton)
      ↓
Versioning          (assign a course_version)
      ↓
Course Modules      (flesh out each module: description, lock toggle, lessons, quizzes)
      ↓
Thumbnail           (cover image/video via Add Media modal)
      ↓
Quality Check        (admin-defined checklist, auto-validated + manual review)
      ↓
Preview and Submit  → course.status = 'SUBMITTED' (see section 4)
```

Collaborators can be invited at any point via the top bar — it's not gated by wizard position, so don't couple it to the step sequence.

---

## 3. Step-by-step

### Step 0 — Legal agreements

One checkbox gate before anything else. Simple, but I'd still persist it rather than trust the frontend:

| Field | Model | Notes |
|---|---|---|
| `legal_agreed` | `Course` | Set `True` + stamp `legal_agreed_at` on submit |

Create the `Course` row here (`status='draft'`, `owner=request.user`). Everything downstream just updates this row.

### Step 1 — Category & topic

Two dropdowns, each with a "request new" escape hatch.

| Field | Model | Notes |
|---|---|---|
| `category` | `Course.category` FK | `SET NULL` on delete — don't cascade-delete courses if a category gets removed |
| `topic` | `Course.topic` FK | Filtered by `category` — validate server-side that `topic.category_id == category_id`, don't trust the client to have filtered correctly |
| — | `CategoryRequest` | Separate write path when the user hits "Request new category" — this doesn't touch `Course` at all, it's a queue for admins |

`Skip` just leaves both FKs null and moves the step pointer forward — don't block progression on this being filled.

### Step 2 — Course title

| Field | Model |
|---|---|
| `title` | `Course.title` |
| `description` | `Course.description` |

Nothing tricky here, but this is the first step where "Quality Check" later cross-references length — the screenshot showed `description` failing a minimum-length check, so whatever validator you write for Quality Check should share the same rule as any client-side character counter, or you'll get a "passed on the frontend, flagged at review" bug.

### Step 3 — Course information

This is the dense one:

| Field | Model | Notes |
|---|---|---|
| `difficulty_level` | `Course.difficulty_level` | enum: beginner/intermediate/advanced |
| `overview` | `Course.overview` | |
| Learning objectives (repeatable) | `CourseLearningObjective` | Ordered list, `course_id` FK — treat as a full replace-on-save from the frontend (delete + bulk-create) rather than diffing, it's simpler and this list is never large |
| Tags | `Tag` + `CourseTag` (M2M) | Reuse `Tag` across courses; get-or-create by slug on the backend, don't let the frontend send raw tag IDs it invented |

### Step 4 — Course Outline

Deceptively simple screen — it only asks for module **titles**, nothing else. I initially assumed this and "Course Modules" were the same screen; they're not. This is the lightweight skeleton:

| Field | Model | Notes |
|---|---|---|
| Module title | `Module.title` | `description`, lessons etc. stay null until step 6 |
| Order | `Module.order_index` | Drag-to-reorder — send the full ordered list on every reorder, don't try to diff positions client-side |

The UI shows "Minimum of 5 required per modules" — that's a **soft validation** surfaced here but I'd actually enforce it as a `QualityCheckCriterion` at the Quality Check step, not a hard block here. Users clearly can save fewer than 5 (the screenshot shows 4) and continue — so don't put a DB constraint on module count, just a checklist warning later.

### Step 5 — Versioning

One dropdown.

| Field | Model | Notes |
|---|---|---|
| `version` | `Course.version` FK → `CourseVersion` | Lookup table, not free text — treat it like `Category`: admin-seeded values (`v1.0`, `v1.1`...) |

### Step 6 — Course Modules (the real module editor)

This is where the Course Outline skeleton gets fleshed out. Each module gets:

| Field | Model |
|---|---|
| `title`, `description` | `Module` |
| `is_locked` | `Module.is_locked` — short-lived editor lease |
| `collaboration_locked` | Creator-controlled persistent freeze that prevents collaborator writes |
| Module objectives | `ModuleLearningObjective` |
| Lessons (add/reorder/delete) | `Lesson` |

**Adding a lesson** picks a type up front — Video / Quiz / Text (`Lesson.content_type`) — and opens the full lesson editor:

| Field | Model | Notes |
|---|---|---|
| `title` | `Lesson.title` | |
| Add Media block | `Lesson.video_file` / `Lesson.embedded_link` | Upload **or** paste a Vimeo/YouTube/Wistia/Typeform link — mutually exclusive in the UI but I wouldn't add a DB constraint forcing that; just validate at the serializer level |
| Video script | `Lesson.video_script_file` | Subtitle/transcript `.srt` upload |
| Lesson Objectives (repeatable) | `LessonObjective` | Same replace-on-save pattern as course objectives |
| Lessons Requirement | `LessonRequirement` | This is rich text, rendered via a WYSIWYG toolbar (Normal text / B / I / U / lists) — store as HTML or Markdown, your call, but be consistent with whatever `lesson_content_block.text_content` uses |
| Body content | `LessonContentBlock` (ordered) | **This is the part that's easy to miss.** The lesson body isn't one big textarea — it's assembled from typed blocks (Heading 1/2, Paragraph, Number list, Bullet list, Blockquote, Divider, Image, Video, Embed, Quiz) via a right-hand block picker. The rendered Preview screen (headings like "Definition", "Types of computer", inline images) is just these blocks rendered in order. Model each block as its own row with `order_index` + `block_type`, not as one field — you'll want this if you ever build a "duplicate this lesson" or "reorder sections" feature |
| Quiz | `Quiz` → `Question` → `QuestionOption` | One quiz per lesson (`quiz.lesson_id` is unique). This is the same Quiz Builder modal covered in step 7 below, just embedded inline here |

A quick gotcha: the module detail screen shows an aggregate "Total Quiz (12)" and a merged question list across all lessons in that module. Don't build a separate module-level quiz table for this — it's just `Question.objects.filter(quiz__lesson__module=module)`, aggregated in the serializer.

### Step 6a — Quiz Builder (embedded, but worth its own section)

Per question:

| Field | Model |
|---|---|
| `question_text` | `Question.question_text` |
| `point` | `Question.point` |
| `question_type` | `Question.question_type` (`multiple_choice` / `essay`) |
| Options (if multiple_choice) | `QuestionOption.text`, `.is_correct` |
| Explanation | `Question.explanation` |

Two things I'd enforce at the DB layer, not just in the serializer, because quiz correctness matters:
- Exactly one `is_correct=True` option per question — partial unique index, not app-level validation alone.
- `quiz.lesson_id` unique — one quiz per lesson, matching the modal's framing ("Customize your quiz questions for **this lesson**").

The right-hand "Quiz summary" panel (total questions, total points, multiple-choice vs essay counts) is entirely derived — don't persist those numbers anywhere, compute them in the serializer or a model property. Persisted derived data goes stale the moment someone edits a question.

### Step 7 — Thumbnail

Single Add Media modal, but with more source options than a plain file upload:

| Field | Model | Notes |
|---|---|---|
| Cover media | `CourseThumbnail` | Own table, not a flat field on `Course` — because source varies: local upload, Google Drive, YouTube, Dropbox, or a pasted link. `source` enum + a check constraint that the right field (`file` vs `external_url`) is populated for the chosen source |
| Active flag | `CourseThumbnail.is_active` | Partial unique index keeps only one active thumbnail per course, while letting you keep history if they replace it |

Constraints shown in the UI (JPEG/PNG, min 1280×720, 16:9) — validate these server-side on upload, not just as a frontend hint, especially aspect ratio since that's easy to fake by resizing.

### Step 8 — Quality Check

This is the step I'd spend the most design time on, because the client explicitly wants it **admin-extensible** — new checklist items without a deploy.

Two tables, not one:

- `QualityCheckCriterion` — the template. Admin-managed, grouped by `section` (Course information, Course Outline, Version, Course Modules, Thumbnail), with `order_index` and `is_active` so old criteria can be retired without losing history on courses that were already checked against them.
- `CourseQualityCheck` — one row per `(course, criterion)`, storing `is_checked` and an optional `warning_note` (e.g. "Your description does not meet the minimum requirement").

The workflow: whenever the course is saved at any step, re-run validation against every active criterion and upsert the `CourseQualityCheck` rows. Don't compute this lazily only when the Quality Check screen loads — the sidebar badge (orange warning icon on "Course Information") needs to reflect state from other steps too, so it has to be a background/on-save recalculation, not a page-specific check.

`Preview and Submit` calls `course_service.submit_course`, which runs the structural standards and moves the course from `DRAFT` to `SUBMITTED`. That is the wizard's finish line - everything after it belongs to the review flow (section 4).

---

## 4. Review flow

The statuses a `Course` really stores are `DRAFT`, `SUBMITTED`, `IN_REVIEW`, `NEEDS_REVISION`, `AWAITING_VIDEO`, `QA_VERIFICATION`, `APPROVED` and `PUBLISHED` (`ARCHIVED` and `REJECTED` exist in the enum but are never stored). Every move is a service call; there are no model signals. A decision is a `ReviewAction`; the person accountable for a seat is a `ReviewAssignment` (one row per course and seat); a rejection's flagged issues are `ReviewFlag` rows. The older design of `CourseReview` / `CourseReviewFlag` / `CourseQualityCheck` described in earlier versions of this document was never built.

There are two review flows, chosen by the platform setting `PlatformSettings.staged_review_flow_enabled` (off by default; it can only be switched while no course is in review).

### Original flow (switch off)

```
DRAFT -> SUBMITTED (First Review) -> IN_REVIEW -> SUBMITTED (Second Review) -> ... (Verification)
      -> QA_VERIFICATION -> APPROVED -> PUBLISHED
```

The video is part of the submission (a preview video is required, BR-015). First and Second Review take a Creator Reviewer, Verification a Verifier, QA a QA Reviewer; whoever decided an earlier seat cannot take a later one (four eyes). A rejection at any seat returns the course to `DRAFT` and a resubmission restarts at First Review with every seat cleared. Anyone holding `courses.set_pricing` / `courses.publish` can price and publish.

### Staged flow (switch on)

Text is reviewed first and the video is added afterwards:

```
DRAFT --submit (text only, no video)--> SUBMITTED / First Review (R1, a Writer reads the text)
R1 approves --> AWAITING_VIDEO
   creator course:    the creator chooses (POST /courses/{id}/video-decision/) to add the video
                      themselves (CREATOR) or leave it to video production (PRODUCTION_ENGINE)
   developer course:  COURSE_TEXT_APPROVED webhook; the developer sends it with
                      POST /mie/v1/submissions/{id}/course/video/ (DEVELOPER)
   platform crawler:  video production supplies it (PRODUCTION_ENGINE)
video submitted (POST /courses/{id}/submit-video/) --> SUBMITTED / Second Review (R2, a Writer watches the video)
R2 approves --> SUBMITTED / Verification (Verifier checks text and video) --> QA_VERIFICATION
QA approves --> APPROVED --> the Approver sets prices and publishes ("final production") --> PUBLISHED

Any seat rejects --> NEEDS_REVISION, remembering the seat (Course.revision_seat)
resubmit (POST /courses/{id}/submit/) --> back at that seat only; earlier seats keep their decisions
```

Rules that differ from the original flow:

- A course submitted with any video (preview video, lesson video or embed, video content block, video media asset) is refused until it has reached the video stage (`Course.video_attached_at`); once it has, the preview video and a media reference on every video lesson are required instead. One function, `quality_check_service.validate_structural_standards`, applies whichever rule fits, so the reviewers' quality check agrees with submission.
- **A course is visible only to the role at its current seat, and to the Super Admin** (`review_service.seat_visibility_q`). First Review and the video review show it to Writers, Verification to Verifiers, QA to QA Reviewers, and an approved course to the Approver; it reaches each role only as it arrives. `NEEDS_REVISION` shows to the roles of the seat that sent it back. Published courses stay visible to everyone with course access. Drafts and `AWAITING_VIDEO` courses belong to the creator, developer or production engine, so no reviewer role sees them. The rule is applied in the query on the review queue (lists, screens, detail and every action), `admin/courses`, the creator-side `/courses/` for staff who view every course, global search and the reviewer dashboard counts, so a hidden course is a 404, never a 403.
- Seats are taken by role: Writers sit First and Second Review (`review_service.STAGED_SEAT_ROLES`), a Verifier Verification, a QA Reviewer QA. Four eyes still applies.
- Only the Approver (and the Super Admin) may set prices and publish (`course_service.require_approver`). Publishing hands the course to `production_engine.finalize`: with `production_enabled` on, that queues a PACKAGE run; with it off, nothing is produced and SoluDesk is marked live at the click.
- A creator's course must start from a topic that exists and is active (`course_service.require_approved_topic`): an existing unreserved topic, or one an admin approved after the creator's request (`/topic-reservations/`). A developer's course is gated on its approved idea instead.
- A `VIDEO` lesson may be written as a script with no media; the media is demanded when the video is submitted.
- An appeal against a rejection (`/course-appeals/`) works on a `NEEDS_REVISION` course too; an approved appeal resumes at the rejecting seat.

The Production Engine (`api/production`) picks up a course whose video production supplies, and does final production for every published course.

- **Runs.** A run is quoted against `PlatformSettings.production_course_budget` and executed behind the `production_enabled` kill switch, on the `production` queue (`worker-media`, which has ffmpeg).
- **VIDEO runs.** A VIDEO run goes through four steps:
  - storyboards each lesson: verbatim narration slices, with the model only grouping sentences;
  - voices each scene: ElevenLabs, then Google Chirp 3 HD;
  - draws each scene with Pillow templates, plus OpenAI illustrations for IMAGE scenes;
  - assembles every lesson into one encode at −16 LUFS, captions it from the voice timings, and runs the quality gate (`lesson_video_service.quality_failures`).

  It also makes the trailer and thumbnail. Every step is content-addressed (`ProductionAsset.key`), so retries and rework pay only for what changed.
- **Delivery.** `production_service._deliver` writes the lesson videos, captions and `MediaAsset` evidence. It then calls `course_service.deliver_video`, which sends the course to Second Review; after a rework it calls `resubmit_produced_video`, which returns it to the rejecting seat.
- **Rework.** A rejection at a video seat whose flags are all engine-fixable starts a rework run (`production_service.handle_rejection`, `FLAG_ACTIONS`). Content flags wait for the author. The author's resubmission remakes the changed lessons before the course goes back (`remake_after_resubmission`).
- **PACKAGE runs.** A PACKAGE run builds the canonical package (`packaging_service.build_package`) and the SCORM 1.2 and 2004 exports. It then delivers to each channel through its active `ChannelMapping`: an API push for SoluDesk, upload kits for Udemy and Coursera. Udemy refuses fully AI (crawler) courses.
- **Catalogue.** The public catalogue (`/catalogue/courses/`) lists published courses that are live on at least one channel.

Not built yet: payout to developers; unpublishing or editing after publication; adaptive streaming (HLS) and C2PA signing of the engine's video.

Dashboard summary numbers are all counts over `Course.status`; they are computed live per request and keyed by every `CourseStatus`, so the new statuses appear without any change.

`Course.quality_score` (the % bar in the course table) is persisted directly on `Course`, since it is shown in a sortable/filterable column.

---

## 5. Collaborators — two different systems, don't conflate them

This tripped me up until the actual screenshots came in, so worth flagging explicitly:

- **`WorkspaceCollaborator`** — account-level. Lives under its own sidebar nav item ("Collaborators"), next to "My Courses" and "Draft". This is the creator's overall team roster: everyone they've ever worked with, each with a platform-wide `role` (`admin` / `author` / `collaborator`), plus profile snapshot fields (`sex`, `country_of_origin`) captured at invite time. Filterable by date range and role.

- **`CourseCollaborator`** — per-course. Triggered from "Invite collaborators" in the lesson editor top bar, scoped to one `course_id`, with a course-specific `role` (`owner`/`editor`/`reviewer`/`viewer`).

The natural next step (not built yet) is letting `CourseCollaborator` invites pull from your existing `WorkspaceCollaborator` list instead of typing an email from scratch every time — but I haven't seen that UI yet, so I left them as two independent tables rather than guessing at the join.

I don't have the actual "+ Invite" modal (just the empty state and the resulting list), so the exact invite fields (email + role, presumably) are inferred from the list columns, not confirmed from a form screenshot.

---

## 6. Implementation notes I'd bring up in standup

- **Ordering fields everywhere** (`order_index` on modules, lessons, questions, options, objectives, content blocks) — every one of these needs a composite unique index on `(parent_id, order_index)` so two siblings can't silently collide on position. Reordering from the frontend should send the full new order, not incremental moves.
- **UUID vs SERIAL** — `Course`, `Module`, `Lesson`, `Quiz`, `Question`, `QuestionOption` are UUID since they get exposed in URLs/API responses. Everything else (lookups, join tables, objective/requirement rows) is `SERIAL` since it's internal-only and never referenced directly by the frontend.
- **Autosave means small transactions.** Every step should be its own atomic update, not one giant "create course" transaction at the end. If the block editor fails halfway through saving 10 blocks, don't leave the lesson half-written — wrap the block list replace in `transaction.atomic()`.
- **Publishing is one-way.** `publish_course` creates a `PublishedCourseSnapshot` and marks the SoluDesk `CourseDistribution` published; there is no unpublish, archive or edit-after-publish yet. Coursera and Udemy distribution rows stay `QUEUED` because nothing pushes to them.
