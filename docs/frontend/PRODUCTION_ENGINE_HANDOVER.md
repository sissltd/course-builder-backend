# Production Engine: frontend handover

The Production Engine (PE) makes the video for courses whose video nobody
else supplies:
- creator courses whose creator chose "leave it to video production";
- every MIE-crawler course.

It also does **final production** for every published course: it builds the
course package and delivers it to the channels the Approver chose.

What the engine makes:
- **Faceless video:** slides, diagrams, code and illustrations, with an
  English stock AI voice and captions. There is no presenter on screen.
- **Delivery:** it sends the video to the second review seat by itself.
- **Fixes:** it fixes the media issues reviewers flag.
- **Packaging:** it builds the course package, SCORM exports and upload
  kits, and pushes the course to SoluDesk.

Everything is behind a kill switch that ships **off**.

For the backend side, see:
- [docs/backend/PRODUCTION_ENGINE.md](../backend/PRODUCTION_ENGINE.md): how it works, every setting, the channel-mapping rules, costs;
- [docs/backend/PRODUCTION_ENGINE_RUNBOOK.md](../backend/PRODUCTION_ENGINE_RUNBOOK.md): switching it on, and fixing each failure.

## Platform settings (`GET` / `PATCH /api/v1/platform-settings/`)

| Field | Default | Meaning |
|---|---|---|
| `production_enabled` | `false` | Kill switch. Off: runs are still created and quoted, but none starts, and a running one pauses before its next step. Off also means publishing marks SoluDesk live at the click, as before. |
| `production_course_budget` | `"100.00"` | Most the engine may spend on one course, in USD. A run whose quote is higher is **blocked** before any spend; a run stops if its spend reaches it. |
| `production_min_caption_accuracy` | `"95.00"` | Lowest caption accuracy (percent) a lesson may have before it goes to review. |
| `production_max_av_drift_ms` | `100` | Largest audio/video length gap a lesson may have, in ms. |
| `production_visual_check_enabled` | `true` | A vision model checks a frame of every scene before review (about a cent per lesson). |
| `production_broll_per_lesson` | `0` | Most faceless AI motion clips per lesson, 0–5. 0 = off. About $1.20 a clip, included in the quote. Needs `GEMINI_API_KEY` on the server; without it, 0 is used whatever is set. |
| `course_duration_min_minutes` | `30` | Courses can be as short as 30 minutes. `course_duration_max_minutes` has no hard ceiling. |

`PATCH` needs `platform.edit_settings` (Admin, Super Admin).

## The course's journey

1. **Video is requested.** A run is created when:
   - a creator calls `POST /courses/{id}/video-decision/ {"will_provide": false}` (sending `true` later cancels the run); or
   - a Writer approves a **crawler** course's text at First Review.

   The run is quoted first: about $0.18 per finished minute plus $3 per course. Over the budget means status `BLOCKED`.
2. **The engine makes the video.** For each lesson:
   - it plans the scenes; the narration is the approved script, word for word;
   - it voices and draws them, then renders the video;
   - it runs the **quality check**: resolution, frame rate, loudness of −16 LUFS, audio/video drift, black frames, long silences, caption accuracy, and a **visual check** of one frame per scene (text readable and spelt right, nothing cut off, no people);
   - with b-roll on, a few scenes per lesson are short faceless motion clips instead of stills.

   A lesson that fails the check is made once more with the next voice. If it fails again, the run fails and production managers are alerted. The engine also makes a 60–120 s trailer from the course description and objectives, and a thumbnail (used only if the course has none).
3. **The course goes to review.**
   - It goes to Second Review exactly as a creator's video submission would: status `SUBMITTED`, `review_stage = SECOND_REVIEW`.
   - The creator is notified: "Video ready".
   - Each lesson's `video_url` and captions are set, plus the QA evidence (`MediaAsset` rows: measured loudness, drift, caption accuracy and accessibility).
4. **A reviewer rejects with flags.** This applies at Second Review, Verification or QA, on a course whose video the engine made.
   - **The engine fixes it** when every flag is a media type:
     - `PRONUNCIATION`, `VOICE_QUALITY` and `PACING`: the lesson is voiced again.
     - `VISUAL_ERROR`, `ON_SCREEN_TEXT` and `VISUAL_QUALITY`: the lesson's scenes are planned and drawn again.
     - `AUDIO_LEVEL` and `CAPTIONS`: the lesson is rendered again.

     Only the flagged lessons are redone, or the flagged module's lessons, or every lesson for a course-wide flag. The engine then resubmits to the seat that rejected it, and the creator gets "Video reworked".
   - **The author fixes it** when any flag is a content type (`CONTENT_ACCURACY`, `CONTENT_CLARITY` or `SCRIPT_LENGTH`), or `OTHER`, or there are no flags. The author edits and resubmits as usual. Because the engine made the video, the resubmission first remakes the lessons whose script changed. The course stays `NEEDS_REVISION` meanwhile, and the creator gets "Video being updated". The engine then resubmits to the rejecting seat.
5. **Publishing triggers final production.** The Approver publishes, and the engine:
   - builds the package and the exports;
   - pushes the course to SoluDesk;
   - builds upload kits for Udemy and Coursera.

## Reviewer UI: flags

Both reject endpoints take `flags`:
- `POST /review-queue/{id}/reject/`;
- `POST /review-queue/{id}/qa-reject/`, which now accepts `flags` too.

Each flag has the shape `[{flag_type, title, system_message, reviewer_note, lesson_id, module_id}]`. Offer `flag_type` as a picker over:

`CONTENT_ACCURACY`, `CONTENT_CLARITY`, `SCRIPT_LENGTH`, `PRONUNCIATION`,
`VOICE_QUALITY`, `AUDIO_LEVEL`, `PACING`, `VISUAL_ERROR`, `ON_SCREEN_TEXT`,
`VISUAL_QUALITY`, `CAPTIONS`, `OTHER`.

For `PRONUNCIATION`, ask for one `term = how to say it` per line in
`reviewer_note`, for example `Kubernetes = koo-ber-NET-eez`. The engine
remembers each one for the course. Captions keep the correct spelling.
Old free-text `flag_type` values are still accepted.

## Admin endpoints

All admin endpoints are under `/api/v1/admin/production/`.
- "Read" means `mie.view_pipeline` or `production.manage`.
- "Manage" means `production.manage`.

Both are held by Admin and Super Admin by default.

### Runs

| Method and path | Who | What |
|---|---|---|
| `GET runs/?status=&course=&page=&size=` | Read | Runs, newest first, under `data.results`. |
| `GET runs/{id}/` | Read | One run plus `lessons[]`: `{lesson_id, title, scene_count, steps_completed, steps_failed}`. |
| `POST runs/{id}/retry/` | Manage | Re-queues a `FAILED` or `BLOCKED` run, re-quoted. Finished work is reused. 409 `production_over_budget` / `production_run_conflict`. No body. |
| `POST runs/{id}/cancel/` | Manage | Cancels a `QUEUED` / `RUNNING` / `BLOCKED` run. 409 once finished. No body. |

**Run object fields:**
- `id`, `course {id, title, status}`, `requested_by` (email);
- `kind`: `VIDEO` or `PACKAGE`;
- `status`, `status_reason`;
- `instructions`: for a rework, `{"lessons": {"<lesson id>": ["REVOICE" | "RESTORYBOARD" | "RERENDER"]}, "flags": [...]}`;
- `quote_amount`, `budget_amount`, `spent_amount` (USD strings);
- `attempts`, `started_at`, `finished_at`, `created_datetime`, `updated_datetime`.

**Statuses:**

| Status | Meaning |
|---|---|
| `QUEUED` | Waiting. If the switch is off, `status_reason` says so. Also used briefly when a provider hiccups or a script was edited mid-run: the run goes round again. |
| `RUNNING` | A worker is on it. A long course takes hours. |
| `BLOCKED` | Budget. Needs an admin. |
| `COMPLETED` | Done. A `VIDEO` run has delivered (or the course had moved on). A `PACKAGE` run has delivered to every channel it could; check each channel's status. |
| `FAILED` | Gave up: the quality check failed twice, a provider refused, or 3 attempts failed. `status_reason` says why (e.g. `visual check, scene 3 (CODE): …`). The runbook has the fix for each reason. Retry it. |
| `CANCELLED` | Stopped. |

### Channel mappings (Production → Channels screen)

A mapping is versioned data that says how a channel receives a course:
- the channel's **JSON Schema** (`target_schema`);
- a **field map** from the course package to the channel's fields;
- a **delivery method**: `API_PUSH` (SoluDesk) or `UPLOAD_KIT` (Udemy, Coursera).

Version 1 of each channel is seeded. SoluDesk's v1 is a draft until
SoluDesk shares its schema.

| Method and path | Who | What |
|---|---|---|
| `GET channel-mappings/?channel=` | Read | Every version, newest first per channel. One per channel has `is_active: true`. |
| `POST channel-mappings/` | Manage | Saves a new version: `{channel, delivery_method, target_schema, field_map, response_id_path?, notes?, activate?}`. 400 if the schema isn't valid JSON Schema or a rule is malformed. Returns 201. |
| `POST channel-mappings/{id}/activate/` | Manage | Makes this version active (also the rollback). Idempotent. No body. |
| `POST channel-mappings/{id}/preview/` | Manage | `{course_id}` → `{payload, gaps[]}`. Shows what the channel would receive and every problem. Nothing is sent. |

The editor should offer a "Preview on course" button before "Activate". The
Swagger description of `POST channel-mappings/` documents the field-map
rules and transforms.

### Packages and channels (course page, Production tab)

| Method and path | Who | What |
|---|---|---|
| `GET courses/{id}/package/` | Read | `files[]`: `{kind, channel, size_bytes, built_at, url}` for the newest `PACKAGE`, `SCORM_12`, `SCORM_2004` and each `UPLOAD_KIT`. Also `lessons[]`: `{lesson_id, title, video_url, captions_vtt_url, captions_srt_url}`. Links last 10 minutes, so re-fetch rather than store them. |
| `POST courses/{id}/package/` | Manage | Builds and delivers again; channels already live are left alone. Use after setting up a channel or activating a mapping. 409 unless the course is `PUBLISHED`. Returns 201 and the `PACKAGE` run. |
| `POST distributions/{id}/published/` | Manage | `{external_course_id}`: records that a person uploaded the kit, and marks that channel `PUBLISHED`. 409 unless the course is published. |

**Channel statuses after final production** (`distribution_channels` on the
course):

| Channel | What happens |
|---|---|
| SoluDesk | `PUBLISHED` once the push succeeds, with `external_course_id`. `FAILED` with `failure_reason` if `SOLUDESK_API_URL` isn't set, SoluDesk refuses, or the mapping finds gaps. |
| Udemy | Stays `QUEUED` with an upload kit ready. Show "Download kit" and "Mark published". `FAILED` for a crawler (fully AI) course: Udemy refuses those. |
| Coursera | Same as Udemy (partner submission). |

Failures alert production managers ("Distribution needs attention").

## Public course catalogue (no login)

| Method and path | What |
|---|---|
| `GET /api/v1/catalogue/courses/?category=&topic=&level=&search=&page=&size=` | Courses that are `PUBLISHED` **and** live on at least one channel, newest first, under `data.results`. `category` / `topic` are slugs. `level` is `BEGINNER` / `INTERMEDIATE` / `ADVANCED`. `search` is at most 100 characters. Unknown `level` → 400. |
| `GET /api/v1/catalogue/courses/{slug}/` | The course page. Anything not listed is a 404. |

**List row fields:**
- `slug`, `title`, `description`;
- `category`, `category_slug`, `topic`, `level`, `duration_minutes`;
- `thumbnail_url`;
- `disclosure {ai_narration, ai_generated_content, statement}`;
- `channels[] {channel, channel_label, price, promotional_price, pricing_model, external_course_id, published_at}`;
- `published_at`.

**The detail page adds:**
- `learning_objectives`, `trailer_url`;
- `outline[] {title, order, lessons[] {title, order, duration_minutes, content_type}}`;
- `json_ld` (put it in a `<script type="application/ld+json">`).

**Behaviour to know:**
- **Signed links:** `thumbnail_url` and `trailer_url` are signed links valid for an hour.
- **Caching:** responses send `Cache-Control: public, max-age=300`.
- **Auth:** no login, and a stale token in the header is ignored.
- **Throttling:** 120 requests a minute per IP.
- **Disclosure:** whenever `disclosure.statement` is non-empty, show it on the course page. Both the EU AI Act and Udemy's policy expect it.
- **Slugs:** each course gets a `slug` when it is published. It never changes, so links keep working.

## Notifications

`mie_pipeline_alert` (Settings → Notifications) covers:
- "Production blocked by budget";
- "Production failed";
- "Distribution needs attention".

All three go to holders of `production.manage`.

Creators get:
- "Video ready": the video went to review;
- "Video reworked": the engine fixed flagged media;
- "Video being updated": their resubmission is being remade.

## Permissions

`production.manage` ("Manage Production Runs") is held by Admin and Super
Admin, and implies `mie.view_pipeline`. No new codenames in this release.
