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
reviews/        # QualityCheckCriterion, CourseQualityCheck, CourseReview, CourseReviewFlag
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
Preview and Submit  → course.status = 'in_review'
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
| `is_locked` | `Module.is_locked` — "prevent collaborators from editing this module" |
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
| Options (if multiple_choice) | `QuestionOption.text`, `.explanation`, `.is_correct` |
| Explanation (if essay) | `Question.explanation` |

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

`Preview and Submit` flips `course.status` from `draft` → `in_review`. That's the wizard's finish line — anything past this point (`needs_revision`, `rejected`, `approved`) belongs to the review flow, not the creation flow.

---

## 4. Review flow (admin side, surfaces on the creator dashboard)

Once a course is `in_review`, an admin (out of scope for this pass — that's the superadmin system we're building later) makes a decision. What I've modeled here is just enough to render the dashboard's "Course details" panel correctly:

| Field | Model |
|---|---|
| Decision (`approved`/`rejected`/`needs_revision`) | `CourseReview.decision` |
| Overall note | `CourseReview.overall_note` |
| Specific flagged issue (e.g. "P1 Lesson 2 — Script Length") | `CourseReviewFlag.title` |
| System-generated message (e.g. "306/500 words below minimum") | `CourseReviewFlag.system_message` |
| Reviewer's note | `CourseReviewFlag.reviewer_note` |
| Which lesson/module it's about | `CourseReviewFlag.lesson_id` / `.module_id` (both nullable — a flag can be course-wide) |

I made this a **review round** model (`CourseReview` has many `CourseReviewFlag`s) rather than flags hanging directly off `Course`, because a course can go through revision more than once — you want history of "what was wrong in round 1 vs round 2," not just the current set of flags overwriting the last.

Dashboard summary numbers (`Total courses`, `In Review`, `Needs Revision`, `Rejected`, `Draft/In Progress`) are all just `Course.objects.filter(owner=request.user, status=...).count()` — don't persist these either.

`Course.quality_score` (the % bar in the course table) — this one I did persist directly on `Course`, since it's shown in a sortable/filterable table column and recomputing it per row on every dashboard load would be wasteful. Recalculate it whenever `CourseQualityCheck` rows change, same trigger point as the quality check re-validation above.

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
- **Publish is not in this schema.** `Course.status` includes `published`/`archived` for completeness, but the actual publish action (superadmin review, visibility settings, etc.) is a separate system we're building later — don't wire a `PublishView` against this schema yet.
