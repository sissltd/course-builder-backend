# Production Engine: runbook

How to switch the engine on, keep an eye on it, and fix what goes wrong.
For how it works, see [PRODUCTION_ENGINE.md](PRODUCTION_ENGINE.md).

---

## Switching it on (first time)

1. **Set the keys** in the server's `.env`:
   - `ELEVENLABS_API_KEY`, or `GOOGLE_TTS_API_KEY`, or both (both is best:
     one is the fallback);
   - `SOLUDESK_API_URL` and `SOLUDESK_API_KEY`;
   - optional: `GEMINI_API_KEY` for b-roll;
   - optional: `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` for tracing.
2. **Deploy**, including the `worker-media` service in `compose.prod.yaml`.
   It renders video and needs the most CPU, so it runs one run at a time.
3. **Check the worker is up:**
   `docker compose -f compose.prod.yaml logs worker-media | tail` should
   show `celery@... ready` and the `production` queue.
4. **Load SoluDesk's real schema** as a new channel mapping version: preview
   it on a course, then activate it. See PRODUCTION_ENGINE.md §8.
5. **Switch it on:** Platform Settings → `production_enabled` = on.
6. **Try one short course end to end** (30 minutes is enough), and compare
   its `spent_amount` with its quote.

## Switching it off

Platform Settings → `production_enabled` = off.
- A running run stops before its next step and goes back to the queue.
- Queued runs wait.
- Nothing is lost: switch it on again and they carry on where they stopped.
- Publishing goes back to the old behaviour: SoluDesk is live at the click.

---

## Watching it

| Where | What to look at |
|---|---|
| Admin → APE Pipeline | Steps per stage, failures |
| `GET /api/v1/admin/production/runs/?status=FAILED` | Runs needing attention |
| A run's detail | Per-lesson `steps_completed` / `steps_failed` |
| Notifications | "Production blocked by budget", "Production failed", "Distribution needs attention" go to everyone with `production.manage` |
| Langfuse (if on) | Scores per lesson; filter by prompt version |
| `ProductionCost` (cost charts) | Spend by category: VOICE, VIDEO, TEXT |

**How long things take:** rendering runs at about 3–4× real time on 2 CPUs,
so a 4-hour course takes roughly 1–1.5 hours.

---

## When something goes wrong

| You see | Why | Do this |
|---|---|---|
| Run `BLOCKED`, "quote … above the per-course budget" | The course is longer than the budget allows | Raise `production_course_budget`, then **Retry** the run. |
| Run `BLOCKED`, "Spend reached the per-course budget" | Retakes, illustrations or b-roll cost more than expected | Raise the budget, or lower `production_broll_per_lesson`, then **Retry**. Finished lessons are kept. |
| Run `FAILED`, "No narration voice is configured" | No voice key | Set `ELEVENLABS_API_KEY` or `GOOGLE_TTS_API_KEY`, restart workers, **Retry**. |
| Run `FAILED`, "Voice provider refused the request (HTTP 401/402)" | Bad key, or out of credit | Fix the account, **Retry**. |
| Run `FAILED`, "failed the quality check: caption accuracy …" | The voice garbled words twice, often an unusual term | Add a pronunciation (a reviewer flag `PRONUNCIATION` with `term = spoken form`, or Django admin → Pronunciations), then **Retry**. |
| Run `FAILED`, "… loudness …" or "… drift …" | Unusual audio from a vendor | **Retry**. If it repeats, check `ffmpeg -version` on `worker-media`. |
| Run `FAILED`, "visual check, scene N (TYPE): …" | A frame looks wrong twice in a row | For CODE or BULLETS scenes, the text is too long for the template: shorten it in the script, or flag `ON_SCREEN_TEXT` to replan. For IMAGE or BROLL, **Retry** to get new visuals. If the check is wrong, switch `production_visual_check_enabled` off for that run. |
| Run `FAILED`, "Lesson '…' has no script to narrate" | An empty lesson | The author adds the script. |
| Run sits in `QUEUED`, "Paused: the Production Engine is switched off" | The switch is off | Switch it on. |
| Run sits in `QUEUED` with no reason | No worker is consuming `production`, or it's waiting for the beat sweep (every minute) | Check `worker-media` and `beat` are running. |
| Run stuck `RUNNING` with no progress | The worker died mid-lesson | The sweep re-queues it after 45 minutes without a heartbeat. Finished lessons are reused. |
| SoluDesk `FAILED`, "No API endpoint is configured … SOLUDESK_API_URL" | Not set | Set it, then **Package again** on the course. |
| SoluDesk `FAILED`, "refused the course (HTTP 4xx): …" | SoluDesk rejected the payload | Read the reason, fix the SoluDesk mapping (new version, preview, activate), then **Package again**. |
| Any channel `FAILED`, "Mapping vN found gaps: …" | The course lacks something the channel requires, e.g. Udemy's 30 minutes or 5 lectures | Fix the course, or the mapping if the rule is wrong, then **Package again**. |
| Udemy `FAILED`, "entirely AI-generated" | A crawler course; Udemy's policy | Expected. Nothing to do. |
| Udemy or Coursera `QUEUED` | Waiting for a person | Download the kit (course → Production → package), upload it on the platform, then **Mark published** with the platform's course id. |

**Retry, Package again and Mark published:**
- **Retry:** `POST /admin/production/runs/{id}/retry/`
- **Package again:** `POST /admin/production/courses/{id}/package/`
- **Mark published:** `POST /admin/production/distributions/{id}/published/`

---

## Common changes

| Change | How |
|---|---|
| Different stock voice | Set `ELEVENLABS_VOICE_ID` (or `GOOGLE_TTS_VOICE`) and restart workers. Only lessons voiced after the change use it; earlier videos are kept. |
| Stricter or looser quality | Platform Settings: caption accuracy, drift, visual check. |
| Turn b-roll on | Set `GEMINI_API_KEY`, then raise `production_broll_per_lesson`. |
| New platform | A new channel mapping (PRODUCTION_ENGINE.md §8). For an API platform, a developer also adds its URL and key settings to `PUSH_ENDPOINTS`. |
| Change the slide look | Edit `api/production/media/slides.py` **and** bump `TEMPLATE_VERSION`, or old frames are reused. |
| Change the encode | Edit `media/ffmpeg.py` **and** bump `RENDER_VERSION`. |
| Change a prompt | Edit it **and** bump its `*_PROMPT_VERSION`, then compare scores in Langfuse. |
| Update vendor prices | `PRODUCTION_*_USD_*` in `.env`. These only change recorded costs; the quote rate lives in `production_service.QUOTE_USD_PER_FINISHED_MINUTE`. |
