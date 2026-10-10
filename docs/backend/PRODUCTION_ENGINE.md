# Production Engine: reference

The Production Engine (PE) turns an approved course script into finished,
checked video. It then packages every published course and delivers it to
its sales channels. This page explains how it works and lists every knob.

- For running it day to day, see [PRODUCTION_ENGINE_RUNBOOK.md](PRODUCTION_ENGINE_RUNBOOK.md).
- For the screens and endpoints, see [../frontend/PRODUCTION_ENGINE_HANDOVER.md](../frontend/PRODUCTION_ENGINE_HANDOVER.md).

---

## 1. The big picture

```
Creator leaves video to the engine ─┐
Writer approves a crawler course ───┴─► VIDEO run ─► Second Review ─► Verification ─► QA ─► Approver publishes
                                           ▲              │               │           │            │
                                           └── rework ◄───┴── media flags ┴───────────┘            ▼
                                                                                              PACKAGE run
                                                                                     SoluDesk · Udemy kit · Coursera kit
                                                                                     SCORM 1.2 · SCORM 2004 · catalogue
```

- **A run** is one job for one course. There are two kinds:
  - **VIDEO** makes, or reworks, the lesson videos, the trailer and the thumbnail;
  - **PACKAGE** builds the final package and delivers it to the channels.
- **Every run is quoted before it spends anything.** A quote over the budget
  blocks the run.
- **One switch controls everything:** `production_enabled` in Platform
  Settings. It ships **off**. While off, runs are created and quoted but
  never start, and publishing works exactly as it did before the engine.
- **One course, one active run.** A database constraint enforces it.

---

## 2. What a VIDEO run does

For each lesson, in course order, the run takes eight steps. Each one is
recorded as a `PipelineJob` (`stage` plus `step`), so the APE Pipeline screen
shows real progress.

| # | Step | What happens | Vendor |
|---|---|---|---|
| 1 | `storyboard` | The script is split into sentences **in code**. The model only groups them into scenes and writes the on-screen text, bullets, code or diagram steps. A plan that skips, repeats or reorders a sentence is rejected and asked for again (2 tries). | OpenAI (`OPENAI_TEXT_MODEL`) |
| 2 | `narration` | Each scene is voiced. Pronunciation fixes (`term = spoken form`) change only what the voice is given. | ElevenLabs, then Google Chirp 3 HD |
| 3 | `visuals` | Each scene becomes a 1920x1080 frame drawn from a template: title, bullets, diagram, code, quote, comparison or recap. IMAGE scenes get an illustration. BROLL scenes get an 8-second faceless motion clip (§5). | Pillow; OpenAI Images; Veo |
| 4 | `render` | The whole lesson is encoded once: each frame held for its narration plus a 0.5 s pause, H.264/AAC 1080p30, loudness normalised to −16 LUFS in two passes, `+faststart`, and an AI disclosure in the file metadata. | ffmpeg |
| 5 | captions | SRT and WebVTT are cut from the **approved** words, timed by the voice's own character timings. A transcript is written too. | — |
| 6 | `quality_check` | The file is measured, a transcription scores caption accuracy, and a vision model looks at one frame per scene (§4). | ffmpeg; OpenAI |
| 7 | store | Only a lesson that passed is stored. | DigitalOcean Spaces |
| 8 | retake | A lesson that failed is made once more, with the next voice and fresh generated visuals. If it fails again, the run fails with the reasons. | — |

After the lessons, the run makes:
- **the trailer:** 60–120 s, title and module cards over narration of the
  course description and objectives, which is approved text only;
- **the thumbnail:** 1280x720, used only if the course has none.

**Delivery** happens last. The run writes each lesson's `video_url` and
captions, plus the `MediaAsset` evidence QA reads, and then:

| Course was | Engine calls | Course goes to |
|---|---|---|
| `AWAITING_VIDEO` | `course_service.deliver_video` | Second Review (`SUBMITTED`) |
| `NEEDS_REVISION` (rework) | `course_service.resubmit_produced_video` | The seat that rejected it |

The actor on both is the engine's own account
(`production-engine@soludesks.invalid`): inactive, no password, no
permissions.

If a lesson script was edited while the run worked, delivery is refused and
the run goes round again. Stale video is never delivered.

---

## 3. Nothing is paid for twice

Every piece the engine makes is stored once, under the SHA-256 of
everything that went into it (`ProductionAsset.key`). Before any step does
work, it looks its hash up.

| Situation | What gets redone |
|---|---|
| A run is retried after failing at lesson 7 | Lessons 8 onwards only |
| A reviewer flags one lesson's pronunciation | That lesson's narration and render |
| The author fixes the text of one lesson | That lesson only |
| Packaging again with nothing changed | Nothing; the SCORM zips are reused |

The course id is part of every hash, so courses never share files. Changing
how frames look (`slides.TEMPLATE_VERSION`) or how videos are encoded
(`ffmpeg.RENDER_VERSION`) changes every hash, and everything is remade.

---

## 4. The quality gate

A lesson must pass **all** of these to reach a reviewer.

| Check | Rule | Where it is set |
|---|---|---|
| Resolution | 1920x1080 | `lesson_video_service.quality_failures` |
| Frame rate | 30 fps | `ffmpeg.FPS` |
| Codecs | H.264 video, AAC audio | code |
| Loudness | −16 LUFS ± 1 LU | `ffmpeg.TARGET_LUFS`, `LOUDNESS_TOLERANCE_LU` |
| Audio/video drift | ≤ 100 ms | Platform Settings `production_max_av_drift_ms` |
| Black frames | none lasting 2 s or more | `ffmpeg.BLACK_MIN_SECONDS` |
| Silence | none lasting 3 s or more | `ffmpeg.SILENCE_MIN_SECONDS` |
| Caption accuracy | ≥ 95% of words right | Platform Settings `production_min_caption_accuracy` |
| Visual check | No scene with problems | Platform Settings `production_visual_check_enabled` |

**Caption accuracy** is 1 − word error rate between the approved script and
what a transcription model hears in the finished audio. It catches a voice
that skipped, garbled or mispronounced words.

**The visual check** takes one frame from the middle of every scene, from
the rendered file itself, and asks a vision model about it. It looks for:
- text cut off, overlapping or unreadable;
- text that differs from what the scene should say;
- garbled text;
- any person, face or hands (the video is faceless);
- stray text or logos inside an illustration or clip;
- blank or broken frames.

It costs about a cent per lesson.

The measurements become the course's `MediaAsset` rows: duration,
resolution, loudness, drift, caption accuracy, subtitle URL and
accessibility. QA's existing checklist (`required_media_failures`) is
therefore already satisfied for an engine course.

---

## 5. AI b-roll

B-roll is short **faceless** motion footage (places, objects, processes)
used instead of a still for a few scenes.

- **Off by default.** Turn it on with `production_broll_per_lesson` (0–5) and
  a `GEMINI_API_KEY`.
- **The model chooses where.** When b-roll is on, the storyboard may mark
  scenes `BROLL`, up to that number per lesson. Extra ones become
  illustrations instead.
- **Clips come from Veo** (`VEO_MODEL`), 8 seconds each, with people
  generation switched off. Their own sound is dropped; the narration plays
  over them.
- **A clip is looped or cut** to fit its scene's narration.
- **If Veo fails or filters the prompt,** the scene is drawn as a still. The
  run carries on.
- **Cost:** about $1.20 a clip. It is added to the quote, so the budget still
  holds.

---

## 6. Rework after a rejection

Reviewers flag issues with a fixed `flag_type` vocabulary (`ReviewFlagType`).
Both reject endpoints accept flags, including QA's.

| Flag type | Who fixes it | Engine action |
|---|---|---|
| `PRONUNCIATION`, `VOICE_QUALITY`, `PACING` | Engine | `REVOICE`: voice the lesson again |
| `VISUAL_ERROR`, `ON_SCREEN_TEXT`, `VISUAL_QUALITY` | Engine | `RESTORYBOARD`: plan and draw again |
| `AUDIO_LEVEL`, `CAPTIONS` | Engine | `RERENDER`: render again |
| `CONTENT_ACCURACY`, `CONTENT_CLARITY`, `SCRIPT_LENGTH`, `OTHER`, or no flags | The author | — |

- **The engine acts only when every flag is one of its types.** It then
  redoes just the flagged lessons: the flag's lesson, its module's lessons,
  or all lessons for a course-wide flag. Then it resubmits to the same seat.
- **The engine never changes approved words.** When the author fixes the text
  and resubmits, the engine remakes only the lessons whose script changed,
  then resubmits.
- **A creator's own video** (`video_provider = CREATOR`) is never touched by
  the engine.
- **Pronunciation:** write one `term = spoken form` per line in the flag's
  `reviewer_note`. It is saved for the whole course (`PronunciationEntry`).

---

## 7. Final production (PACKAGE run)

When the Approver publishes with the engine on, the PACKAGE run:

1. **Builds the canonical package** (`packaging_service.build_package`).
   It is one JSON document describing the course for every destination:

   | Key | Holds |
   |---|---|
   | `course` | id, slug, title, description, category, topic, level, language, duration, objectives, thumbnail and trailer links, creator, version, published date, counts |
   | `modules[]` | title, description, quiz, and `lessons[]` with title, type, minutes, objectives, `video_url`, caption links, transcript, quiz |
   | `final_quiz` | the course exam |
   | `pricing[]` | each channel's price, promotional price and pricing model |
   | `disclosure` | `ai_narration`, `ai_generated_content`, and the `statement` to show learners |
   | `channel` | added per channel: that channel's own price row |

   Media links in it are signed for 7 days, the most storage allows.
2. **Builds SCORM 1.2 and SCORM 2004 zips.** The videos and captions sit
   inside the zip, and a small player marks a lesson complete at 90% watched.
   Any corporate LMS can import them.
3. **Delivers to each channel the Approver chose,** through that channel's
   active mapping (§8):

   | Channel | Method | Result |
   |---|---|---|
   | SoluDesk | API push to `SOLUDESK_API_URL` | `PUBLISHED` with SoluDesk's course id, or `FAILED` with the reason |
   | Udemy | Upload kit | Stays `QUEUED` until an admin records the upload |
   | Coursera | Upload kit | Same as Udemy |

   Udemy refuses courses that are entirely AI-generated, so a crawler course
   is marked `FAILED` for Udemy with that reason.
4. **Alerts production managers** if any channel failed.

A channel that is already `PUBLISHED` is never sent again. Packaging again
after fixing a channel is therefore always safe.

---

## 8. Channel mappings: the "schemaless" layer

Each platform is described by **data, not code**. A `ChannelMapping` version
has:

- `target_schema`: the JSON Schema of what the platform accepts;
- `field_map`: where each of the platform's fields comes from in the
  package;
- `delivery_method`: `API_PUSH` or `UPLOAD_KIT`;
- `response_id_path`: for a push, where the platform's course id is in its
  answer, for example `data.id`.

Versions are never edited. A change is a new version, and only one version
per channel is active. Version 1 of each channel is seeded. SoluDesk's v1
is a **draft** until SoluDesk shares its real schema.

### Field map rules

| Rule | Example | Meaning |
|---|---|---|
| `from` | `{"from": "course.title"}` | Read a value from the package. Dots walk into objects. |
| `from` + `transform` | `{"from": "course.title", "transform": ["truncate:60"]}` | Then change it. |
| `from` + `default` | `{"from": "course.topic", "default": "General"}` | Use this when the value is missing. |
| `const` | `{"const": "English"}` | A fixed value. |
| `each` + `map` | `{"each": "modules", "map": {"name": {"from": "title"}}}` | One mapped object per list item. Inside, paths start at the item; a leading `/` starts at the package root. |

Target field names use dots to nest: `"price.amount": {...}` produces
`{"price": {"amount": ...}}`.

### Transforms

| Transform | Does |
|---|---|
| `truncate:N` | Cut to N characters, ending with "…" |
| `upper`, `lower`, `strip` | Case and whitespace |
| `join:SEP` | Join a list with SEP (default ", ") |
| `count` | Length of a list |
| `first_sentence` | Up to the first `.`, `!` or `?` |
| `minutes_to_seconds` | × 60 |
| `string`, `number`, `integer` | Convert the type |

### Adding or changing a platform

1. `POST /api/v1/admin/production/channel-mappings/` with the schema and
   field map, without `activate`.
2. `POST .../channel-mappings/{id}/preview/ {"course_id": "..."}` on a real
   course. Fix the mapping until `gaps` is empty.
3. `POST .../channel-mappings/{id}/activate/`. To roll back, activate the
   old version.
4. For a new **API push** channel, a developer adds its URL and key settings
   to `packaging_service.PUSH_ENDPOINTS`. URLs and keys always come from the
   server environment, never from the mapping, so mapping data can never
   send a secret somewhere else.

---

## 9. Evaluation tracing (Langfuse)

With `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` set, every run is a
Langfuse trace (trace id = run id).

**Generations:** every vendor call is recorded with its model, input,
output, usage, cost and time:
- `storyboard`, `narration`, `illustration`, `broll`;
- `caption_check`, `visual_check`.

**Scores, per lesson:**

| Score | Meaning |
|---|---|
| `caption_accuracy` | 0–100 |
| `loudness_lufs` | Measured loudness |
| `av_drift_ms` | Measured drift |
| `visual_check_passed` | 1 or 0 |
| `storyboard_first_try` | 1 if the first plan covered the script |
| `qc_attempts` | 1, 2, or 3 for failed |

**Prompt versions** (`STORYBOARD_PROMPT_VERSION`,
`VISUAL_CHECK_PROMPT_VERSION`) are in each generation's metadata. In
Langfuse, filter by version to see whether a prompt change helped.

**Best effort:** tracing never fails a run. A Langfuse outage is logged and
ignored.

**Privacy:** course text is sent. Use a self-hosted `LANGFUSE_HOST` if it
must stay in-house.

---

## 10. Settings

### Platform Settings (admin screen, `PATCH /platform-settings/`)

| Field | Default | What it does |
|---|---|---|
| `production_enabled` | off | The kill switch. |
| `production_course_budget` | $100 | Most one course may cost. Checked at the quote and after every step. |
| `production_min_caption_accuracy` | 95% | Quality gate. |
| `production_max_av_drift_ms` | 100 | Quality gate. |
| `production_visual_check_enabled` | on | Frame-by-frame visual check. |
| `production_broll_per_lesson` | 0 | AI clips per lesson (0 = off, at most 5). |
| `course_duration_min_minutes` | 30 | Shortest course allowed. There is no maximum ceiling. |

### Environment (`.env`)

| Variable | Needed for | Default |
|---|---|---|
| `OPENAI_API_KEY`, `OPENAI_TEXT_MODEL`, `OPENAI_IMAGE_MODEL` | Storyboards, visual check, illustrations, caption check | — (already set) |
| `ELEVENLABS_API_KEY` | Primary voice | empty |
| `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL_ID` | Which stock voice and model | `JBFqnCBsd6RMkjVDRZzb`, `eleven_multilingual_v2` |
| `GOOGLE_TTS_API_KEY`, `GOOGLE_TTS_VOICE` | Fallback voice | empty, `en-US-Chirp3-HD-Charon` |
| `GEMINI_API_KEY`, `VEO_MODEL` | B-roll | empty, `veo-3.1-fast-generate-preview` |
| `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Tracing | `https://cloud.langfuse.com`, empty, empty |
| `SOLUDESK_API_URL`, `SOLUDESK_API_KEY` | SoluDesk delivery | empty |
| `PRODUCTION_*_USD_*` | The list prices used to record costs | see `.env.example` |

At least one voice key is required. With neither, VIDEO runs fail with
that reason.

---

## 11. Costs

| Item | Price used | Per finished minute (about) |
|---|---|---|
| Storyboard | $5 / $30 per million tokens in / out | $0.01 |
| Narration (ElevenLabs) | $0.10 per 1,000 characters | $0.09 |
| Caption check | $0.003 per minute | $0.003 |
| Visual check | text-model tokens | $0.002 |
| Illustrations | $0.06 each | depends on IMAGE scenes |
| B-roll | $0.15 per second | $1.20 per clip, when on |
| Rendering | our own CPU | — |

**The quote** is $0.18 per finished minute plus $3 per course, plus the
b-roll allowance when b-roll is on.

| Course length | Quote (b-roll off) |
|---|---|
| 30 min | $8.40 |
| 2 h | $24.60 |
| 4 h | $46.20 |

Actual spend is in `ProductionCost` and on the run (`spent_amount`).

---

## 12. Where the code is

| Path | What |
|---|---|
| `api/production/models.py` | Runs, scenes, assets, pronunciations, channel mappings |
| `api/production/services/production_service.py` | The run lifecycle, delivery, rework |
| `api/production/services/lesson_video_service.py` | Narration → visuals → render → quality gate; trailer; thumbnail |
| `api/production/services/packaging_service.py` | Package, SCORM, channel delivery, kits |
| `api/production/services/channel_mapping_service.py` | The mapper |
| `api/production/services/run_ledger.py` | Steps, costs, budget and switch checks, alerts |
| `api/production/services/asset_store.py` | The content-addressed store |
| `api/production/media/` | Frames (Pillow), ffmpeg, captions, scoring |
| `api/production/providers/` | Storyboard, voices, illustration and transcription, b-roll, visual check |
| `api/production/tracing.py` | Langfuse |
| `api/courses/services/catalogue_service.py` | The public catalogue |

**Tests** are in `api/production/tests/`. Only the vendors are faked:
- a voice that speaks a real tone;
- a transcription vendor that hears perfectly, or not;
- a vision model that sees nothing wrong, or the problems it is given;
- storage in a directory.

Rendering, measuring and the quality gate run for real.
